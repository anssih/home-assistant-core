"""Test trigger template entity."""

import asyncio
from unittest.mock import Mock, patch

import pytest

from homeassistant.components.template import DATA_COORDINATORS, DOMAIN, trigger_entity
from homeassistant.components.template.coordinator import TriggerUpdateCoordinator
from homeassistant.const import (
    CONF_ICON,
    CONF_NAME,
    CONF_STATE,
    EVENT_HOMEASSISTANT_STOP,
    SERVICE_RELOAD,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, ServiceCall, State
from homeassistant.helpers import condition, template
from homeassistant.helpers.script import Script
from homeassistant.helpers.trigger_template_entity import CONF_PICTURE
from homeassistant.helpers.typing import ConfigType
from homeassistant.setup import async_setup_component

from .conftest import async_trigger

from tests.common import assert_setup_component, mock_restore_cache_with_extra_data

_ICON_TEMPLATE = 'mdi:o{{ "n" if value=="on" else "ff" }}'
_PICTURE_TEMPLATE = '/local/picture_o{{ "n" if value=="on" else "ff" }}'
_RESTORE_ENTITY_NAME = "test_attribute_restore"
_RESTORE_EVENT = "test_attribute_restore"


@pytest.mark.parametrize(
    ("domain", "state_template", "expected_state", "extra_data", "managed_attributes"),
    [
        pytest.param(
            "sensor",
            "ready",
            "ready",
            {"native_value": "ready", "native_unit_of_measurement": None},
            {"state_class": "measurement", "last_reset": "2026-01-01T00:00:00+00:00"},
            id="sensor",
        ),
        pytest.param(
            "binary_sensor",
            "{{ true }}",
            STATE_ON,
            {"auto_off_time": None},
            {},
            id="binary_sensor",
        ),
    ],
)
@pytest.mark.parametrize(
    ("attributes", "expected_attributes"),
    [
        pytest.param(
            {"count": "{{ this.attributes.get('count', 0) + 1 }}"},
            {"count": 41},
            id="mapping",
        ),
        pytest.param(
            "{{ dict(count=this.attributes.get('count', 0) + 1) }}",
            {
                "count": 41,
                "payload": {"history": ["saved"]},
            },
            id="template",
        ),
    ],
)
async def test_restore_attributes_before_trigger(
    hass: HomeAssistant,
    domain: str,
    state_template: str,
    expected_state: str,
    extra_data: ConfigType,
    managed_attributes: ConfigType,
    attributes: dict[str, str] | str,
    expected_attributes: ConfigType,
) -> None:
    """Restore attributes before the first trigger and expose them through this."""
    entity_id = f"{domain}.{_RESTORE_ENTITY_NAME}"
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State(
                    entity_id,
                    expected_state,
                    {
                        "count": 41,
                        "payload": {"history": ["saved"]},
                        "friendly_name": "Old name",
                        "icon": "mdi:old",
                        "entity_picture": "/local/old.png",
                        "device_class": "humidity",
                        "unit_of_measurement": "%",
                        "supported_features": 0,
                        **managed_attributes,
                    },
                ),
                extra_data,
            ),
        ),
    )
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            DOMAIN: {
                "triggers": {"trigger": "event", "event_type": _RESTORE_EVENT},
                domain: {
                    "name": _RESTORE_ENTITY_NAME,
                    "state": state_template,
                    "attributes": attributes,
                },
            }
        },
    )
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes == {**expected_attributes, "friendly_name": "Old name"}
    entity = hass.data[domain].get_entity(entity_id)
    assert entity.extra_state_attributes == expected_attributes

    hass.bus.async_fire(_RESTORE_EVENT)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes == {"count": 42, "friendly_name": _RESTORE_ENTITY_NAME}


@pytest.mark.parametrize(
    ("domain", "state_template", "expected_state"),
    [
        pytest.param("sensor", "ready", "ready", id="sensor"),
        pytest.param("binary_sensor", "{{ true }}", STATE_ON, id="binary_sensor"),
    ],
)
@pytest.mark.parametrize(
    "attributes",
    [
        pytest.param(
            {"count": "{{ this.attributes.get('count', 0) + 1 }}"}, id="mapping"
        ),
        pytest.param(
            "{{ dict(count=this.attributes.get('count', 0) + 1) }}", id="template"
        ),
    ],
)
async def test_reload_preserves_attributes(
    hass: HomeAssistant,
    domain: str,
    state_template: str,
    expected_state: str,
    attributes: dict[str, str] | str,
) -> None:
    """Preserve trigger template attributes on reload and on the next trigger."""
    entity_id = f"{domain}.{_RESTORE_ENTITY_NAME}"
    config = {
        DOMAIN: {
            "triggers": {"trigger": "event", "event_type": _RESTORE_EVENT},
            domain: {
                "name": _RESTORE_ENTITY_NAME,
                "state": state_template,
                "attributes": attributes,
            },
        }
    }
    assert await async_setup_component(hass, DOMAIN, config)
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    hass.bus.async_fire(_RESTORE_EVENT)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["count"] == 1

    with patch(
        "homeassistant.config.load_yaml_config_file", autospec=True, return_value=config
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
        await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == expected_state
    assert state.attributes["count"] == 1

    hass.bus.async_fire(_RESTORE_EVENT)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["count"] == 2


class TestEntity(trigger_entity.TriggerEntity):
    """Test entity class."""

    domain = "test"
    __test__ = False
    _entity_id_format = "test.{}"
    extra_template_keys = (CONF_STATE,)
    _state_option = CONF_STATE

    @property
    def state(self) -> bool | None:
        """Return extra attributes."""
        return self._rendered.get(self._state_option)


async def test_reference_blueprints_is_none(hass: HomeAssistant) -> None:
    """Test template entity requires hass to be set before accepting templates."""
    coordinator = TriggerUpdateCoordinator(hass, {})
    entity = trigger_entity.TriggerEntity(hass, coordinator, {})

    assert entity.referenced_blueprint is None


async def test_template_state(hass: HomeAssistant) -> None:
    """Test manual trigger template entity with a state."""
    config = {
        CONF_NAME: template.Template("test_entity", hass),
        CONF_ICON: template.Template(_ICON_TEMPLATE, hass),
        CONF_PICTURE: template.Template(_PICTURE_TEMPLATE, hass),
        CONF_STATE: template.Template("{{ value == 'on' }}", hass),
    }

    coordinator = TriggerUpdateCoordinator(hass, {})
    entity = TestEntity(hass, coordinator, config)
    entity.entity_id = "test.entity"

    coordinator._execute_update({"value": STATE_ON})
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert entity.state == "True"
    assert entity.icon == "mdi:on"
    assert entity.entity_picture == "/local/picture_on"

    coordinator._execute_update({"value": STATE_OFF})
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert entity.state == "False"
    assert entity.icon == "mdi:off"
    assert entity.entity_picture == "/local/picture_off"


async def test_bad_template_state(hass: HomeAssistant) -> None:
    """Test manual trigger template entity with a state."""
    config = {
        CONF_NAME: template.Template("test_entity", hass),
        CONF_ICON: template.Template(_ICON_TEMPLATE, hass),
        CONF_PICTURE: template.Template(_PICTURE_TEMPLATE, hass),
        CONF_STATE: template.Template("{{ x - 1 }}", hass),
    }
    coordinator = TriggerUpdateCoordinator(hass, {})
    entity = TestEntity(hass, coordinator, config)
    entity.entity_id = "test.entity"

    coordinator._execute_update({"x": 1})
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert entity.available is True
    assert entity.state == "0"
    assert entity.icon == "mdi:off"
    assert entity.entity_picture == "/local/picture_off"

    coordinator._execute_update({"value": STATE_OFF})
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert entity.available is False
    assert entity.state is None
    assert entity.icon is None
    assert entity.entity_picture is None


async def test_template_state_syntax_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Test manual trigger template entity when state render fails."""
    config = {
        CONF_NAME: template.Template("test_entity", hass),
        CONF_ICON: template.Template(_ICON_TEMPLATE, hass),
        CONF_PICTURE: template.Template(_PICTURE_TEMPLATE, hass),
        CONF_STATE: template.Template("{{ incorrect ", hass),
    }

    coordinator = TriggerUpdateCoordinator(hass, {})
    entity = TestEntity(hass, coordinator, config)
    entity.entity_id = "test.entity"

    coordinator._execute_update({"value": STATE_ON})
    entity._handle_coordinator_update()
    await hass.async_block_till_done()

    assert f"Error rendering {CONF_STATE} template for test.entity" in caplog.text

    assert entity.state is None
    assert entity.icon is None
    assert entity.entity_picture is None


async def test_script_variables_from_coordinator(
    hass: HomeAssistant, calls: list[ServiceCall], caplog: pytest.LogCaptureFixture
) -> None:
    """Test script variables."""
    await async_trigger(hass, "sensor.start", "1")
    with assert_setup_component(1, DOMAIN):
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                "template": {
                    "variables": {"a": "{{ states('sensor.start') }}", "c": 0},
                    "triggers": {
                        "trigger": "state",
                        "entity_id": ["sensor.trigger"],
                    },
                    "actions": [
                        {
                            "action": "test.automation",
                            "data": {
                                "a": "{{ a }}",
                                "b": "{{ b }}",
                                "c": "{{ c }}",
                            },
                        }
                    ],
                    "sensor": {
                        "name": "test",
                        "state": "{{ 'on' }}",
                        "variables": {"b": "{{ a + 1 }}", "c": 1},
                        "attributes": {
                            "a": "{{ a }}",
                            "b": "{{ b }}",
                            "c": "{{ c }}",
                        },
                    },
                },
            },
        )
    await async_trigger(hass, "sensor.trigger", "anything")

    assert len(calls) == 1
    assert calls[0].data["a"] == 1
    assert calls[0].data["c"] == 0
    assert "'b' is undefined when rendering '{{ b }}'" in caplog.text

    state = hass.states.get("sensor.test")
    assert state
    assert state.state == "on"
    assert state.attributes["a"] == 1
    assert state.attributes["b"] == 2
    assert state.attributes["c"] == 1


async def test_default_entity_id(hass: HomeAssistant) -> None:
    """Test template entity creates suggested entity_id from the default_entity_id."""
    coordinator = TriggerUpdateCoordinator(hass, {})
    entity = TestEntity(hass, coordinator, {"default_entity_id": "test.test"})
    assert entity.entity_id == "test.test"


async def test_bad_default_entity_id(hass: HomeAssistant) -> None:
    """Test template entity creates suggested entity_id from the default_entity_id."""
    coordinator = TriggerUpdateCoordinator(hass, {})
    entity = TestEntity(hass, coordinator, {"default_entity_id": "bad.test"})
    assert entity.entity_id == "test.test"


async def test_multiple_template_validators(hass: HomeAssistant) -> None:
    """Tests multiple templates execute validators."""
    await async_trigger(hass, "sensor.state", "opening")
    await async_trigger(hass, "sensor.position", "50")
    await async_trigger(hass, "sensor.tilt", "49")
    with assert_setup_component(1, DOMAIN):
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                "template": {
                    "triggers": {
                        "trigger": "state",
                        "entity_id": ["sensor.trigger"],
                    },
                    "cover": {
                        "name": "test",
                        "state": "{{ states('sensor.state') }}",
                        "position": "{{ states('sensor.position') }}",
                        "tilt": "{{ states('sensor.tilt') }}",
                        "set_cover_position": [],
                        "set_cover_tilt_position": [],
                        "open_cover": [],
                        "close_cover": [],
                    },
                },
            },
        )
    await async_trigger(hass, "sensor.trigger", "anything")

    state = hass.states.get("cover.test")
    assert state
    assert state.state == "opening"
    assert state.attributes["current_position"] == 50
    assert state.attributes["current_tilt_position"] == 49


async def test_coordinator_shutdown_unloads_script_and_condition(
    hass: HomeAssistant,
) -> None:
    """Test that coordinator shutdown stops and unloads script and condition."""
    coordinator = TriggerUpdateCoordinator(hass, {})

    mock_script = Mock(spec=Script)
    mock_cond = Mock(spec=condition.ConditionsChecker)
    coordinator._script = mock_script
    coordinator._cond_func = mock_cond

    await coordinator.async_shutdown()

    mock_script.async_unload.assert_called_once()
    mock_cond.async_unload.assert_called_once()


async def test_shutdown_stops_script_and_keeps_triggers_subscribed(
    hass: HomeAssistant,
) -> None:
    """Test HA shutdown stops coordinator scripts without unsubscribing."""
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            "template": {
                "trigger": {"platform": "event", "event_type": "test_event"},
                "action": [
                    {"event": "action_event"},
                    {"delay": {"seconds": 120}},
                ],
                "sensor": {
                    "name": "test",
                    "state": "{{ trigger.event.data.value }}",
                },
            }
        },
    )
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    # Verify trigger is active
    listeners = hass.bus.async_listeners()
    assert listeners.get("test_event", 0) == 1

    # Fire the trigger to start the action script, then yield without
    # waiting for the script to finish
    hass.bus.async_fire("test_event", {"value": "hello"})
    await asyncio.sleep(0)

    # Script should be running (stuck on delay)
    coordinators = hass.data[DATA_COORDINATORS]
    assert len(coordinators) == 1
    assert coordinators[0]._script.is_running
    assert not coordinators[0]._script._unloaded

    # Fire shutdown
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()

    # Script should be stopped but not unloaded - this is handled by the script helper
    assert not coordinators[0]._script.is_running
    assert not coordinators[0]._script._unloaded

    # Triggers are not unsubscribed on shutdown
    listeners = hass.bus.async_listeners()
    assert listeners.get("test_event", 0) == 1


async def test_reload_stops_script_and_unsubscribes_triggers(
    hass: HomeAssistant,
) -> None:
    """Test that reloading stops coordinator scripts and unsubscribes old triggers."""
    assert await async_setup_component(
        hass,
        DOMAIN,
        {
            "template": {
                "trigger": {"platform": "event", "event_type": "test_event"},
                "action": [
                    {"event": "action_event"},
                    {"delay": {"seconds": 120}},
                ],
                "sensor": {
                    "name": "test",
                    "state": "{{ trigger.event.data.value }}",
                },
            }
        },
    )
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    # Verify trigger is active
    listeners = hass.bus.async_listeners()
    assert listeners.get("test_event", 0) == 1

    # Fire the trigger to start the action script
    hass.bus.async_fire("test_event", {"value": "hello"})
    await asyncio.sleep(0)

    # Script should be running
    coordinators = hass.data[DATA_COORDINATORS]
    assert len(coordinators) == 1
    coordinator = coordinators[0]
    assert coordinator._script.is_running
    assert not coordinator._script._unloaded

    # Reload with empty config
    with patch(
        "homeassistant.config.load_yaml_config_file",
        autospec=True,
        return_value={"template": []},
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RELOAD, blocking=True)
        await hass.async_block_till_done()

    # Script should be stopped and unloaded
    assert not coordinator._script.is_running
    assert coordinator._script._unloaded

    # Old trigger should be unsubscribed
    listeners = hass.bus.async_listeners()
    assert listeners.get("test_event", 0) == 0


async def test_entity_conditions_with_multiple_entities(hass: HomeAssistant) -> None:
    """Test entity conditions with multiple entities."""
    with assert_setup_component(1, DOMAIN):
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                "template": {
                    "triggers": {
                        "trigger": "state",
                        "entity_id": ["sensor.trigger"],
                    },
                    "sensor": [
                        {
                            "name": "a",
                            "state": "{{ states('sensor.trigger') }}",
                            "conditions": {
                                "condition": "numeric_state",
                                "entity_id": "sensor.trigger",
                                "above": 1,
                            },
                        },
                        {
                            "name": "b",
                            "state": "{{ trigger.to_state.state }}",
                            "conditions": {
                                "condition": "numeric_state",
                                "entity_id": "sensor.trigger",
                                "below": 1,
                            },
                        },
                    ],
                },
            },
        )
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.a")
    assert state
    assert state.state == STATE_UNKNOWN

    state = hass.states.get("sensor.b")
    assert state
    assert state.state == STATE_UNKNOWN

    await async_trigger(hass, "sensor.trigger", "2")

    state = hass.states.get("sensor.a")
    assert state
    assert state.state == "2"

    state = hass.states.get("sensor.b")
    assert state
    assert state.state == STATE_UNKNOWN

    await async_trigger(hass, "sensor.trigger", "0")

    state = hass.states.get("sensor.a")
    assert state
    assert state.state == "2"

    state = hass.states.get("sensor.b")
    assert state
    assert state.state == "0"


async def test_entity_conditions_variables(hass: HomeAssistant) -> None:
    """Test entity conditions variables."""
    await async_trigger(hass, "sensor.start", "0")
    with assert_setup_component(1, DOMAIN):
        assert await async_setup_component(
            hass,
            DOMAIN,
            {
                "template": {
                    "triggers": {
                        "trigger": "state",
                        "entity_id": ["sensor.trigger", "sensor.start"],
                    },
                    "variables": {"a": "{{ states('sensor.start') }}"},
                    "sensor": [
                        {
                            "name": "test",
                            "state": "{{ states('sensor.start') }}",
                            "variables": {"b": "{{ a + 1 }}"},
                            "conditions": {
                                "condition": "template",
                                "value_template": "{{ b > 1 }}",
                            },
                            "attributes": {
                                "a": "{{ a }}",
                                "b": "{{ b }}",
                            },
                        },
                    ],
                },
            },
        )
    await hass.async_block_till_done()
    await hass.async_start()
    await hass.async_block_till_done()

    state = hass.states.get("sensor.test")
    assert state
    assert state.state == STATE_UNKNOWN
    assert "a" not in state.attributes
    assert "b" not in state.attributes

    await async_trigger(hass, "sensor.trigger", "anything")

    state = hass.states.get("sensor.test")
    assert state
    assert state.state == STATE_UNKNOWN
    assert "a" not in state.attributes
    assert "b" not in state.attributes

    await async_trigger(hass, "sensor.start", "1")

    state = hass.states.get("sensor.test")
    assert state
    assert state.state == "1"
    assert state.attributes["a"] == 1
    assert state.attributes["b"] == 2

    await async_trigger(hass, "sensor.start", "0")

    state = hass.states.get("sensor.test")
    assert state
    assert state.state == "1"
    assert state.attributes["a"] == 1
    assert state.attributes["b"] == 2


async def test_entity_remove_unloads_condition(
    hass: HomeAssistant,
) -> None:
    """Test that removing the entity unloads the condition."""
    coordinator = TriggerUpdateCoordinator(hass, {})

    mock_cond = Mock(spec=condition.ConditionsChecker)

    entity = TestEntity(hass, coordinator, {})
    entity._cond_func = mock_cond
    await entity.async_will_remove_from_hass()

    mock_cond.async_unload.assert_called_once()
