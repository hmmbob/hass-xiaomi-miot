import logging
from typing import TYPE_CHECKING, Optional, Callable
from functools import cached_property

from homeassistant.helpers.entity import Entity, EntityCategory
from homeassistant.helpers.restore_state import ExtraStoredData, RestoredExtraData

from .utils import get_customize_via_entity, wildcard_models, CustomConfigHelper
from .miot_spec import MiotService, MiotProperty, MiotAction
from .converters import BaseConv, InfoConv, MiotPropConv, MiotActionConv

if TYPE_CHECKING:
    from .device import Device

_LOGGER = logging.getLogger(__package__)


class BasicEntity(Entity, CustomConfigHelper):
    device: 'Device' = None
    conv: 'BaseConv' = None

    def custom_config(self, key=None, default=None):
        return get_customize_via_entity(self, key, default)


class XEntity(BasicEntity):
    CLS: dict[str, Callable] = {}

    log = _LOGGER
    added = False
    _attr_available = False
    _attr_should_poll = False
    _attr_has_entity_name = True
    _miot_service: Optional[MiotService] = None
    _miot_property: Optional[MiotProperty] = None
    _miot_action: Optional[MiotAction] = None

    def __init__(self, device: 'Device', conv: 'BaseConv'):
        self.device = device
        self.hass = device.hass
        self.conv = conv
        self.attr = conv.attr

        if isinstance(conv, MiotPropConv):
            self.entity_id = conv.prop.generate_entity_id(self, conv.domain)
            self._attr_name = str(conv.prop.friendly_desc)
            self._attr_translation_key = conv.prop.friendly_name
            self._miot_service = conv.prop.service
            self._miot_property = conv.prop

        elif isinstance(conv, MiotActionConv):
            self.entity_id = device.spec.generate_entity_id(self, conv.action.name, conv.domain)
            self._attr_name = str(conv.action.friendly_desc)
            self._attr_translation_key = conv.action.friendly_name
            self._miot_service = conv.action.service
            self._miot_action = conv.action
            self._miot_property = conv.prop
            self._attr_available = True

        else:
            self.entity_id = device.spec.generate_entity_id(self, self.attr, conv.domain)
            # self._attr_name = self.attr.replace('_', '').title()
            self._attr_translation_key = self.attr

        self.listen_attrs: set = {self.attr}
        self._attr_unique_id = f'{device.unique_id}-{convert_unique_id(conv)}'
        self._attr_device_info = self.device.hass_device_info
        self._attr_extra_state_attributes = {
            'converter': f'{conv}'.replace('custom_components.xiaomi_miot.core.miot_spec.', ''), # TODO
        }

        self._attr_icon = conv.option.get('icon')

        if isinstance(conv, InfoConv):
            self._attr_available = True
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self.on_init()
        self.device.add_listener(self.on_device_update)

    @property
    def unique_mac(self):
        return self.device.info.unique_id

    def on_init(self):
        """Run on class init."""

    def on_device_update(self, data: dict):
        state_change = False
        self._attr_available = True

        if isinstance(self.conv, InfoConv):
            self._attr_extra_state_attributes.update(data)

        if keys := self.listen_attrs & data.keys():
            self.set_state(data)
            state_change = True
            for key in keys:
                if key == self.attr:
                    continue
                self._attr_extra_state_attributes[key] = data.get(key)

        if state_change and self.added:
            self._async_write_ha_state()

    def get_state(self) -> dict:
        """Run before entity remove if entity is subclass from RestoreEntity."""
        return {}

    def set_state(self, data: dict):
        """Run on data from device."""
        self._attr_state = data.get(self.attr)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        # filter None values
        if state := {k: v for k, v in self.get_state().items() if v is not None}:
            return RestoredExtraData(state)
        return None

    async def async_added_to_hass(self) -> None:
        self.added = True

        if call := getattr(self, 'async_get_last_extra_data', None):
            data: RestoredExtraData = await call()
            if data and self.listen_attrs & data.as_dict().keys():
                self.set_state(data.as_dict())


    async def async_will_remove_from_hass(self) -> None:
        self.device.remove_listener(self.on_device_update)

    @cached_property
    def customize_keys(self):
        keys = []
        prop = getattr(self.conv, 'prop', None)
        action = getattr(self.conv, 'action', None)
        for mod in wildcard_models(self.device.model):
            if isinstance(action, MiotAction):
                keys.append(f'{mod}:{action.full_name}')
                keys.append(f'{mod}:{action.name}')
            if isinstance(prop, MiotProperty):
                keys.append(f'{mod}:{prop.full_name}')
                keys.append(f'{mod}:{prop.name}')
            if self.attr and not (prop or action):
                keys.append(f'{mod}:{self.attr}')
        return keys


def convert_unique_id(conv: 'BaseConv'):
    action = getattr(conv, 'action', None)
    if isinstance(action, MiotAction):
        return action.unique_name
    prop = getattr(conv, 'prop', None)
    if isinstance(prop, MiotProperty):
        return prop.unique_name
    return conv.attr