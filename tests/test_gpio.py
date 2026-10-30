"""Tests for GPIO controller using FakeGpio."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.gpio import GpioController
from src.models import GpioConfig, SwitchPosition
from tests.conftest import FakeGpio


def make_gpio_config(
    channel_pins: tuple[int, ...] = (17, 22, 23),
    switch_pin: int = 27,
    debounce_ms: int = 200,
    invert_switch: bool = False,
) -> GpioConfig:
    return GpioConfig(
        channel_pins=channel_pins,
        switch_pin=switch_pin,
        debounce_ms=debounce_ms,
        invert_switch=invert_switch,
    )


class TestGpioControllerStart:
    def test_configures_all_channel_pins(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config(channel_pins=(17, 22, 23))
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        controller.start()

        setup_pins = [pin for pin, _ in gpio.setup_calls]
        for pin in (17, 22, 23):
            assert pin in setup_pins
        controller.stop()

    def test_configures_switch_pin(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        controller.start()

        setup_pins = [pin for pin, _ in gpio.setup_calls]
        assert 27 in setup_pins
        controller.stop()

    def test_adds_edge_detect_for_buttons(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config(channel_pins=(17, 22))
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        controller.start()

        button_edges = [reg for reg in gpio.edge_detects if reg.pin in (17, 22)]
        assert len(button_edges) == 2
        for reg in button_edges:
            assert reg.edge == "falling"
        controller.stop()

    def test_adds_edge_detect_for_switch(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        controller.start()

        switch_edge = gpio.get_edge_detect(27)
        assert switch_edge is not None
        assert switch_edge.edge == "both"
        controller.stop()


class TestButtonPress:
    def test_fires_callback_with_correct_index(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config(channel_pins=(17, 22, 23))
        callback = MagicMock()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=callback,
            on_switch_change=MagicMock(),
        )
        controller.start()

        # Simulate button press on pin 22 (channel index 1)
        gpio.simulate_edge(22)

        callback.assert_called_with(1)
        controller.stop()


class TestSwitchEdge:
    def test_fires_callback_on_state_change(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        callback = MagicMock()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=callback,
        )
        # Initial state: HIGH (OFF)
        gpio.set_pin(27, True)
        controller.start()

        # Simulate switch going LOW (OFF when not inverted)
        gpio.set_pin(27, False)
        gpio.simulate_edge(27)

        callback.assert_called_once_with(SwitchPosition.OFF)
        controller.stop()

    def test_ignores_same_state(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        callback = MagicMock()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=callback,
        )
        gpio.set_pin(27, True)
        controller.start()

        # Simulate edge but pin hasn't changed
        gpio.simulate_edge(27)

        callback.assert_not_called()
        controller.stop()

    def test_invert_switch(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config(invert_switch=True)
        callback = MagicMock()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=callback,
        )
        gpio.set_pin(27, True)
        controller.start()

        # Switch goes LOW → with invert, this means ON
        gpio.set_pin(27, False)
        gpio.simulate_edge(27)

        callback.assert_called_once_with(SwitchPosition.ON)
        controller.stop()


class TestGetSwitchPosition:
    def test_high_is_on(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        gpio.set_pin(27, True)
        assert controller.get_switch_position() == SwitchPosition.ON

    def test_low_is_off(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        gpio.set_pin(27, False)
        assert controller.get_switch_position() == SwitchPosition.OFF

    def test_inverted(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config(invert_switch=True)
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        gpio.set_pin(27, True)
        assert controller.get_switch_position() == SwitchPosition.OFF


class TestStopCleanup:
    def test_stop_calls_gpio_cleanup(self) -> None:
        gpio = FakeGpio()
        config = make_gpio_config()
        controller = GpioController(
            config=config,
            gpio=gpio,
            on_channel_button=MagicMock(),
            on_switch_change=MagicMock(),
        )
        controller.start()
        controller.stop()
        assert gpio.cleaned_up is True
