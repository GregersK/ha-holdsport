"""Fælles entity-base for Holdsport."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import device_identifier
from .coordinator import HoldsportCoordinator, ProfileData


class HoldsportEntity(CoordinatorEntity[HoldsportCoordinator]):
    """Entity knyttet til én profil (ét familiemedlem = én enhed)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HoldsportCoordinator,
        account_id: int,
        profile_id: int,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self.profile_id = profile_id
        self._attr_unique_id = f"{account_id}_{profile_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier(account_id, profile_id)},
            name=coordinator.profiles[profile_id].name,
            manufacturer="Holdsport",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def profile_data(self) -> ProfileData | None:
        return (self.coordinator.data or {}).get(self.profile_id)

    @property
    def available(self) -> bool:
        return super().available and self.profile_data is not None
