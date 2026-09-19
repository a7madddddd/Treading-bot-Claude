import unittest

from engine.decision_source import ControllerDecision, DecisionKind, InMemoryDecisionSource, PendingDecisionSource


class TestControllerDecision(unittest.TestCase):
    def test_requires_proposal_id(self):
        with self.assertRaises(ValueError):
            ControllerDecision(proposal_id="", kind=DecisionKind.APPROVE, decided_by="controller")

    def test_requires_decided_by(self):
        with self.assertRaises(ValueError):
            ControllerDecision(proposal_id="P-1", kind=DecisionKind.APPROVE, decided_by="")

    def test_requires_decision_kind_enum(self):
        with self.assertRaises(TypeError):
            ControllerDecision(proposal_id="P-1", kind="approve", decided_by="controller")  # type: ignore[arg-type]

    def test_holds_fields(self):
        decision = ControllerDecision(proposal_id="P-1", kind=DecisionKind.REJECT, decided_by="controller")
        self.assertEqual(decision.proposal_id, "P-1")
        self.assertEqual(decision.kind, DecisionKind.REJECT)
        self.assertEqual(decision.decided_by, "controller")


class TestAbstractInterface(unittest.TestCase):
    def test_cannot_instantiate_abc_directly(self):
        with self.assertRaises(TypeError):
            PendingDecisionSource()  # type: ignore[abstract]


class TestInMemoryDecisionSource(unittest.TestCase):
    def test_poll_returns_empty_when_nothing_submitted(self):
        source = InMemoryDecisionSource()
        self.assertEqual(source.poll(), [])

    def test_poll_drains_submitted_decisions_exactly_once(self):
        source = InMemoryDecisionSource()
        d1 = ControllerDecision(proposal_id="P-1", kind=DecisionKind.APPROVE, decided_by="controller")
        d2 = ControllerDecision(proposal_id="P-2", kind=DecisionKind.REJECT, decided_by="controller")
        source.submit(d1)
        source.submit(d2)

        drained = source.poll()
        self.assertEqual(drained, [d1, d2])

        # A second poll() must not redeliver the same decisions.
        self.assertEqual(source.poll(), [])

    def test_decisions_submitted_after_a_poll_are_delivered_on_the_next_poll(self):
        source = InMemoryDecisionSource()
        source.submit(ControllerDecision(proposal_id="P-1", kind=DecisionKind.APPROVE, decided_by="controller"))
        source.poll()
        d2 = ControllerDecision(proposal_id="P-2", kind=DecisionKind.CONFIRM_LADDER2_PARTIAL_FILL, decided_by="controller")
        source.submit(d2)
        self.assertEqual(source.poll(), [d2])


if __name__ == "__main__":
    unittest.main()
