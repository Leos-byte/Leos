from __future__ import annotations

import unittest

from leos_agent import ManualRecoveryPacket


class ManualRecoveryPacketTests(unittest.TestCase):
    def test_packet_roundtrip_and_rendering_redacts_secret_like_values(self) -> None:
        packet = ManualRecoveryPacket.build(
            step_id="s1",
            tool_name="tool<script>",
            reason="rollback failed for ghp_should_not_leak",
            risk_level="high",
            profile="production_locked_down",
            affected_resources=["repo:o/r"],
            suggested_actions=["inspect", "repair"],
        )

        data = packet.as_dict()
        restored = ManualRecoveryPacket.from_mapping(data)
        markdown = restored.render_markdown()
        html = restored.render_html()

        self.assertEqual(restored.step_id, "s1")
        self.assertIn("tool", markdown)
        self.assertIn("suggested_actions", markdown)
        self.assertNotIn("ghp_should_not_leak", markdown)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)


if __name__ == "__main__":
    unittest.main()
