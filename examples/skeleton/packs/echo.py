"""Echo capability for the walking skeleton (M3.5). Lives OUTSIDE the
chassis package on purpose: roles are data + packs, never chassis code."""

from agent_chassis.packs import CapabilityPack, PackConfig, ToolSpec


class EchoPack(CapabilityPack):
    name = "echo"

    def instructions(self, config: PackConfig) -> str:
        return (
            "Demo capability: when the user asks you to reverse some text,"
            " call reverse_text and show the result. Otherwise just chat"
            " briefly in persona."
        )

    def tools(self, config: PackConfig) -> list[ToolSpec]:
        async def reverse_text(text: str) -> str:
            """Reverse the characters of the given text."""
            return text[::-1]

        # pure=True: no I/O, runs in-workflow instead of as an activity.
        return [ToolSpec("reverse_text", reverse_text, "Reverse a string", pure=True)]


PACKS = [EchoPack()]
