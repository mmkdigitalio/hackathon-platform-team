import * as fs from "node:fs";
import * as path from "node:path";
import type { TreeLayout } from "./catalog";

export interface StackTarget {
  appId: string;
  environment: string;
}

export interface Affected {
  stacks: StackTarget[];
  /** Gelöschte Sollzustandsdateien: kein Destroy, sondern Hinweis auf die separate Außerbetriebnahme. */
  decommission: Array<StackTarget & { file: string }>;
}

const ENV_FILE = /^platform\/environments\/([^/]+)\/([^/]+)\.ya?ml$/;
const CATALOG_FILE = /^platform\/catalog\/([^/]+)\//;
const GLOBAL_PATHS = [/^platform\/schemas\/app-catalog\//, /^platform\/infra\/app-runtime\//];

function existingStacks(layout: TreeLayout): StackTarget[] {
  if (!fs.existsSync(layout.environmentsDir)) return [];
  const out: StackTarget[] = [];
  for (const env of fs.readdirSync(layout.environmentsDir).sort()) {
    const dir = path.join(layout.environmentsDir, env);
    if (!fs.statSync(dir).isDirectory()) continue;
    for (const f of fs.readdirSync(dir).sort()) {
      const m = /^(.+)\.ya?ml$/.exec(f);
      if (m) out.push({ appId: m[1], environment: env });
    }
  }
  return out;
}

/**
 * plan: Sollzustände, Katalogeinträge, Schema und App-Programm wirken auf die zugehörigen Stacks.
 * deploy: nur Sollzustandsänderungen lösen ein Deployment aus (Design §5).
 */
export function affectedStacks(nameStatus: string, mode: "plan" | "deploy", layout: TreeLayout, _repoRoot: string): Affected {
  const stacks = new Map<string, StackTarget>();
  const decommission: Affected["decommission"] = [];
  const all = existingStacks(layout);
  const add = (t: StackTarget) => stacks.set(`${t.appId}/${t.environment}`, t);

  for (const line of nameStatus.split("\n")) {
    if (!line.trim()) continue;
    const [status, ...paths] = line.split("\t");
    const changes: Array<[string, string]> = status.startsWith("R")
      ? [["D", paths[0]], ["A", paths[1]]]
      : [[status[0], paths[0]]];
    for (const [s, p] of changes) {
      const env = ENV_FILE.exec(p);
      if (env) {
        const target = { environment: env[1], appId: env[2] };
        if (s === "D") decommission.push({ ...target, file: p });
        else add(target);
        continue;
      }
      if (mode === "deploy") continue;
      const cat = CATALOG_FILE.exec(p);
      if (cat) {
        all.filter((t) => t.appId === cat[1]).forEach(add);
        continue;
      }
      if (GLOBAL_PATHS.some((r) => r.test(p))) all.forEach(add);
    }
  }
  // Eine im selben Diff wieder angelegte Datei ist keine Außerbetriebnahme
  const remaining = decommission.filter((d) => !stacks.has(`${d.appId}/${d.environment}`));
  return { stacks: [...stacks.values()], decommission: remaining };
}
