"""Xiaomi Gateway subdevice base class."""

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

import attr
import click

from miio import DeviceException
from miio.click_common import command
from miio.push_server import EventInfo

from ..gateway import GATEWAY_MODEL_EU, GATEWAY_MODEL_ZIG3, GatewayCallback

_LOGGER = logging.getLogger(__name__)
if TYPE_CHECKING:
    from ..gateway import Gateway


@attr.s(auto_attribs=True)
class SubDeviceInfo:
    """SubDevice discovery info."""

    sid: str
    type_id: int
    unknown: int
    unknown2: int
    fw_ver: int


class SubDevice:
    """Base class for all subdevices of the gateway these devices are connected through
    zigbee."""

    def __init__(
        self,
        gw: "Gateway",
        dev_info: SubDeviceInfo,
        model_info: Optional[Dict] = None,
    ) -> None:

        self._gw = gw
        self.sid = dev_info.sid
        if model_info is None:
            model_info = {}
        self._model_info = model_info
        self._battery_powered = model_info.get("battery_powered", True)
        self._battery = None
        self._voltage = None
        self._fw_ver = dev_info.fw_ver

        self._model = model_info.get("model", "unknown")
        self._name = model_info.get("name", "unknown")
        self._zigbee_model = model_info.get("zigbee_id", "unknown")

        self._props = {}
        self.get_prop_exp_dict = {}
        for prop in model_info.get("properties", []):
            prop_name = prop.get("name", prop["property"])
            self._props[prop_name] = prop.get("default", None)
            if prop.get("get") == "get_property_exp":
                self.get_prop_exp_dict[prop["property"]] = prop

        self.setter = model_info.get("setter")

        self.push_events = model_info.get("push_properties", [])
        self._event_ids: List[str] = []
        self._registered_callbacks: Dict[str, GatewayCallback] = {}

    def __repr__(self):
        return "<Subdevice {}: {}, model: {}, zigbee: {}, fw: {}, bat: {}, vol: {}, props: {}>".format(
            self.device_type,
            self.sid,
            self.model,
            self.zigbee_model,
            self.firmware_version,
            self.get_battery(),
            self.get_voltage(),
            self.status,
        )

    @property
    def status(self):
        """Return sub-device status as a dict containing all properties."""
        return self._props

    @property
    def device_type(self):
        """Return the device type name."""
        return self._model_info.get("type")

    @property
    def name(self):
        """Return the name of the device."""
        return f"{self._name} ({self.sid})"

    @property
    def model(self):
        """Return the device model."""
        return self._model

    @property
    def zigbee_model(self):
        """Return the zigbee device model."""
        return self._zigbee_model

    @property
    def firmware_version(self):
        """Return the firmware version."""
        return self._fw_ver

    @property
    def battery(self):
        """Return the battery level in %."""
        return self._battery

    @property
    def voltage(self):
        """Return the battery voltage in V."""
        return self._voltage

    @command()
    def update(self):
        """Update all device properties."""
        if self.get_prop_exp_dict:
            values = self.get_property_exp(list(self.get_prop_exp_dict.keys()))
            try:
                i = 0
                for prop in self.get_prop_exp_dict.values():
                    result = values[i]
                    if prop.get("devisor"):
                        result = values[i] / prop.get("devisor")
                    prop_name = prop.get("name", prop["property"])
                    self._props[prop_name] = result
                    i = i + 1
            except Exception as ex:
                raise DeviceException(
                    "One or more unexpected results while "
                    "fetching properties %s: %s on model %s"
                    % (self.get_prop_exp_dict, values, self.model)
                ) from ex

    @command()
    def send(self, command):
        """Send a command/query to the subdevice."""
        try:
            return self._gw.send(command, [self.sid])
        except Exception as ex:
            raise DeviceException(
                "Got an exception while sending command %s on model %s"
                % (command, self.model)
            ) from ex

    @command()
    def send_arg(self, command, arguments):
        """Send a command/query including arguments to the subdevice."""
        try:
            return self._gw.send(command, arguments, extra_parameters={"sid": self.sid})
        except Exception as ex:
            raise DeviceException(
                "Got an exception while sending "
                "command '%s' with arguments '%s' on model %s"
                % (command, str(arguments), self.model)
            ) from ex

    @command(click.argument("property"))
    def get_property(self, property):
        """Get the value of a property of the subdevice."""
        try:
            response = self._gw.send("get_device_prop", [self.sid, property])
        except Exception as ex:
            raise DeviceException(
                "Got an exception while fetching property %s on model %s"
                % (property, self.model)
            ) from ex

        if not response:
            raise DeviceException(
                f"Empty response while fetching property {property!r}: {response} on model {self.model}"
            )

        return response

    @command(click.argument("properties", nargs=-1))
    def get_property_exp(self, properties):
        """Get the value of a bunch of properties of the subdevice."""
        try:
            response = self._gw.send(
                "get_device_prop_exp", [[self.sid] + list(properties)]
            ).pop()
        except Exception as ex:
            raise DeviceException(
                "Got an exception while fetching properties %s on model %s"
                % (properties, self.model)
            ) from ex

        if len(list(properties)) != len(response):
            raise DeviceException(
                "unexpected result while fetching properties %s: %s on model %s"
                % (properties, response, self.model)
            )

        return response

    @command(click.argument("property"), click.argument("value"))
    def set_property(self, property, value):
        """Set a device property of the subdevice."""
        try:
            return self._gw.send("set_device_prop", {"sid": self.sid, property: value})
        except Exception as ex:
            raise DeviceException(
                "Got an exception while setting propertie %s to value %s on model %s"
                % (property, str(value), self.model)
            ) from ex

    @command()
    def unpair(self):
        """Unpair this device from the gateway."""
        return self.send("remove_device")

    @command()
    def get_battery(self) -> Optional[int]:
        """Update the battery level, if available."""
        if not self._battery_powered:
            _LOGGER.debug(
                "%s is not battery powered, get_battery not supported",
                self.name,
            )
            return None

        if self._gw.model not in [GATEWAY_MODEL_EU, GATEWAY_MODEL_ZIG3]:
            self._battery = self.send("get_battery").pop()
        else:
            _LOGGER.info(
                "Gateway model '%s' does not (yet) support get_battery",
                self._gw.model,
            )
        return self._battery

    @command()
    def get_voltage(self) -> Optional[float]:
        """Update the battery voltage, if available."""
        if not self._battery_powered:
            _LOGGER.debug(
                "%s is not battery powered, get_voltage not supported",
                self.name,
            )
            return None

        if self._gw.model in [GATEWAY_MODEL_EU, GATEWAY_MODEL_ZIG3]:
            self._voltage = self.get_property("voltage").pop() / 1000
        else:
            _LOGGER.info(
                "Gateway model '%s' does not (yet) support get_voltage",
                self._gw.model,
            )
        return self._voltage

    @command()
    def get_firmware_version(self) -> Optional[int]:
        """Returns firmware version."""
        try:
            self._fw_ver = self.get_property("fw_ver").pop()
        except Exception as ex:
            _LOGGER.info(
                "get_firmware_version failed, returning firmware version from discovery info: %s",
                ex,
            )
        return self._fw_ver

    def register_callback(self, id: str, callback: GatewayCallback):
        """Register a external callback function for updates of this subdevice."""
        if id in self._registered_callbacks:
            _LOGGER.error(
                "A callback with id '%s' was already registed, overwriting previous callback",
                id,
            )
        self._registered_callbacks[id] = callback

    def remove_callback(self, id: str):
        """Remove a external callback using its id."""
        self._registered_callbacks.pop(id)

    def push_callback(self, action: str, params: str):
        """Push callback received from the push server."""
        if action not in self.push_events:
            _LOGGER.error(
                "Received unregistered action '%s' callback for sid '%s' model '%s'",
                action,
                self.sid,
                self.model,
            )

        event = self.push_events[action]
        prop = event.get("property")
        value = event.get("value")
        if prop is not None and value is not None:
            self._props[prop] = value

        for callback in self._registered_callbacks.values():
            callback(action, params)

    async def subscribe_events(self):
        """subscribe to all subdevice events using the push server."""
        if self._gw._push_server is None:
            raise DeviceException(
                "Can not install push callback without a PushServer instance"
            )

        result = True
        for action in self.push_events:
            event_info = EventInfo(
                action=action,
                extra=self.push_events[action]["extra"],
                source_sid=self.sid,
                source_model=self.zigbee_model,
                event=self.push_events[action].get("event", None),
                command_extra=self.push_events[action].get("command_extra", ""),
                trigger_value=self.push_events[action].get("trigger_value"),
            )

            event_id = await self._gw._push_server.subscribe_event(self._gw, event_info)
            if event_id is None:
                result = False
                continue

            self._event_ids.append(event_id)

        return result

    async def unsubscribe_events(self):
        """Unsubscibe from events registered in the gateway memory."""
        for event_id in self._event_ids:
            await self._gw._push_server.unsubscribe_event(self._gw, event_id)
            self._event_ids.remove(event_id)
