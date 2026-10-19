"""The behavioural factor is offered only for a kind of device the customer has templates for."""
from amfa.engine.availability import available
from amfa.engine.context import (Band, BehaviouralCtx, Capability, Context, DeviceCtx, Enrollment, Factor,
                                 TransactionCtx, behaviour_input)


def ctx(touch: bool, **enrolled) -> Context:
    return Context(userId="u", transactionId="t", transaction=TransactionCtx(amountBand=Band.LOW),
                   device=DeviceCtx(), behavioural=BehaviouralCtx(),
                   capability=Capability(touch=touch), enrollment=Enrollment(**enrolled))


def test_computer_templates_serve_a_computer_only():
    assert available(Factor.UEBA, ctx(False))
    assert not available(Factor.UEBA, ctx(True))


def test_phone_templates_serve_a_phone():
    assert available(Factor.UEBA, ctx(True, keystrokeTouch=True))
    assert not available(Factor.UEBA, ctx(False, keystroke=False, keystrokeTouch=True))


def test_which_set_a_device_uses():
    assert behaviour_input(Capability(touch=True)) == "touch"
    assert behaviour_input(Capability()) == "keyboard"
