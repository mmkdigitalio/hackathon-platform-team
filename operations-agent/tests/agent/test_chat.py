"""Chat-Kanal: Antwort an die meldende Person, genau eine Ergebnisnachricht je Auftrag, Werkzeuge je Kanal."""

from __future__ import annotations

from dataclasses import replace

from contracts import ContextRefs, ConversationRef, OperationsServices

from .conftest import MODEL, World, settings
from .fakes import ScriptedModel, call, say, script


class MemoryConversation:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def post(self, job_id: str, text: str, kind: str) -> str:
        self.messages.append((job_id, kind, text))
        return f"msg-{len(self.messages)}"

    def has_result(self, job_id: str) -> bool:
        return any(j == job_id and k == "result" for j, k, _ in self.messages)


def chat_invocation(world: World):
    return world.invocation(
        context=ContextRefs(
            git_ref="main",
            issue_text="[10:01] Erika: ich bekomme 400er-Fehler beim Hochladen",
            conversation=ConversationRef(id="conv-1", reporter="Erika"),
        )
    )


def run(world: World, chat: MemoryConversation, *steps, inv=None):
    from agent import Dependencies, execute

    services = replace(world.services, conversation=chat) if chat is not None else world.services
    model = ScriptedModel(responder=script(*steps))
    result = execute(
        inv or chat_invocation(world),
        services,
        settings=settings(),
        model=MODEL,
        deps=Dependencies(
            github=world.github.client(), kubernetes=lambda p: world.k8s(p), http=world.probe(), chat_model=model,
            sleep=world.clock.sleep, clock=world.clock,
        ),
    )
    return result, model


def test_reply_and_single_result_message(world: World) -> None:
    chat = MemoryConversation()
    result, _ = run(
        world, chat,
        call("reply_to_reporter", {"text": "Danke, ich prüfe AFFiNE jetzt."}),
        call("get_workload_state"),
        say("Geprüft."),
    )
    kinds = [k for _, k, _ in chat.messages]
    assert kinds == ["reply", "result"]
    assert chat.messages[0][2] == "Danke, ich prüfe AFFiNE jetzt."
    assert result.references["chatResult"] == "msg-2"
    assert any(c.name == "reply_to_reporter" and c.status == "ok" for c in result.tool_calls)


def test_result_not_posted_twice_after_resume(world: World) -> None:
    chat = MemoryConversation()
    chat.post("job-1", "früheres Ergebnis", "result")
    run(world, chat, say("Nichts zu tun."))
    assert [k for _, k, _ in chat.messages] == ["result"], "Wiederaufnahme postet kein zweites Ergebnis"


def test_tools_offered_per_channel(world: World) -> None:
    _, model = run(world, MemoryConversation(), say("ok"))
    names = model.tool_names()
    assert "reply_to_reporter" in names and "comment_on_issue" not in names
    _, model = run(world, None, say("ok"), inv=world.invocation())
    names = model.tool_names()
    assert "reply_to_reporter" not in names


def test_chat_history_reaches_model_as_data(world: World) -> None:
    _, model = run(world, MemoryConversation(), say("ok"))
    prompt = model.first_human_text()
    assert '"kanal": "chat"' in prompt
    assert '<daten quelle="chat-verlauf">' in prompt and "400er-Fehler" in prompt


def test_services_without_conversation_still_valid(world: World) -> None:
    assert OperationsServices(world.actions, world.lock, world.state, world.ownership, world.services.checkpoints).conversation is None
