import * as k8s from "@pulumi/kubernetes";
import * as pulumi from "@pulumi/pulumi";
import { baseDomainFor, platformNamespaces } from "../components/conventions";
import { FoundationConfig } from "./config";

export const ingressClassName = "traefik";
export const clusterIssuerName = "letsencrypt";

export interface IngressArgs {
    cfg: FoundationConfig;
    provider: k8s.Provider;
}

// Shared HTTP(S) entry without an own domain: one Traefik ingress controller behind the
// public IP of its LoadBalancer service, cert-manager with a Let's Encrypt ClusterIssuer
// (HTTP-01), and baseDomain = <ip-with-dashes>.sslip.io. App stacks add Ingress rules
// for their host and request a certificate through the ClusterIssuer annotation.
// Controller choice and rationale: operations/foundation/decisions.md.
export function createIngress({ cfg, provider }: IngressArgs) {
    const opts = { provider };

    const ingressNamespace = new k8s.core.v1.Namespace(
        "ingress",
        { metadata: { name: platformNamespaces.ingress } },
        opts,
    );
    const certNamespace = new k8s.core.v1.Namespace(
        "certificates",
        { metadata: { name: platformNamespaces.certificates } },
        opts,
    );

    // No HTTP-to-HTTPS redirect: plain HTTP over the same hosts is the documented
    // staging fallback when Let's Encrypt does not issue a certificate.
    const traefik = new k8s.helm.v3.Release(
        "traefik",
        {
            name: "traefik",
            chart: "traefik",
            version: cfg.traefikChartVersion,
            namespace: ingressNamespace.metadata.name,
            repositoryOpts: { repo: "https://traefik.github.io/charts" },
            values: {
                ingressClass: { enabled: true, isDefaultClass: true, name: ingressClassName },
                providers: { kubernetesIngress: { publishedService: { enabled: true } } },
                service: {
                    annotations: {
                        // Traefik answers 404 on "/" without a matching route; probe TCP instead of HTTP.
                        "service.beta.kubernetes.io/port_80_health-probe_protocol": "tcp",
                        "service.beta.kubernetes.io/port_443_health-probe_protocol": "tcp",
                    },
                },
            },
        },
        opts,
    );

    const certManager = new k8s.helm.v3.Release(
        "cert-manager",
        {
            name: "cert-manager",
            chart: "cert-manager",
            version: cfg.certManagerChartVersion,
            namespace: certNamespace.metadata.name,
            repositoryOpts: { repo: "https://charts.jetstack.io" },
            values: { crds: { enabled: true } },
        },
        opts,
    );

    new k8s.apiextensions.CustomResource(
        "letsencrypt",
        {
            apiVersion: "cert-manager.io/v1",
            kind: "ClusterIssuer",
            metadata: { name: clusterIssuerName },
            spec: {
                acme: {
                    server: cfg.acmeServer,
                    email: cfg.acmeEmail,
                    privateKeySecretRef: { name: `${clusterIssuerName}-account` },
                    solvers: [{ http01: { ingress: { ingressClassName } } }],
                },
            },
        },
        { provider, dependsOn: [certManager, traefik] },
    );

    // The Helm release waits until the LoadBalancer service has its public IP. The IP
    // stays as long as the service is not replaced (operations/foundation/operations.md).
    const service = k8s.core.v1.Service.get(
        "traefik-service",
        pulumi.interpolate`${traefik.status.namespace}/${traefik.status.name}`,
        { provider, dependsOn: [traefik] },
    );
    const ingressIp = service.status.loadBalancer.ingress.apply((ingress) => {
        const ip = ingress?.[0]?.ip;
        if (!ip) {
            throw new Error("The ingress controller service has no public IP yet; re-run the foundation after it is assigned.");
        }
        return ip;
    });

    return { ingressIp, baseDomain: ingressIp.apply(baseDomainFor) };
}
