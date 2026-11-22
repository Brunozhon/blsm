from typing import Any

PROPERTY_METADATA_VERSION = "26.3"


def _boolean(description: str, category: str = "Gameplay") -> dict[str, Any]:
    return {"type": "boolean", "description": description, "category": category}


def _integer(
    description: str,
    category: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "integer",
        "description": description,
        "category": category,
    }
    if minimum is not None:
        result["min"] = minimum
    if maximum is not None:
        result["max"] = maximum
    return result


def _choice(description: str, options: list[str], category: str) -> dict[str, Any]:
    return {
        "type": "select",
        "description": description,
        "options": options,
        "category": category,
    }


PROPERTY_METADATA: dict[str, dict[str, Any]] = {
    "accepts-transfers": _boolean(
        "Allows players to be transferred here by another Minecraft server.", "Network"
    ),
    "allow-flight": _boolean(
        "Allows flight-capable clients and mods without automatic flight kicks."
    ),
    "allow-nether": _boolean(
        "Obsolete in 26.3. Use the minecraft:allow_entering_nether_using_portals "
        "game rule instead."
    ),
    "broadcast-console-to-ops": _boolean(
        "Shows commands run from the server console to operators.", "Administration"
    ),
    "broadcast-rcon-to-ops": _boolean(
        "Shows commands run through RCON to operators.", "Administration"
    ),
    "bug-report-link": {
        "type": "string",
        "description": "Optional URL shown to players for reporting server bugs.",
        "category": "Presentation",
    },
    "chat-spam-threshold-seconds": _integer(
        "Accumulated seconds of chat spam before a player is kicked; 0 disables "
        "chat-spam kicks.",
        "Security",
        0,
    ),
    "command-spam-threshold-seconds": _integer(
        "Accumulated seconds of command spam before a player is kicked; 0 disables "
        "command-spam kicks.",
        "Security",
        0,
    ),
    "difficulty": _choice(
        "Controls hostile mob damage and survival difficulty.",
        ["peaceful", "easy", "normal", "hard"],
        "Gameplay",
    ),
    "enable-command-block": _boolean(
        "Obsolete in 26.3. Use the minecraft:command_blocks_work game rule instead."
    ),
    "enable-code-of-conduct": _boolean(
        "Requires players to accept a localized policy from the codeofconduct folder.",
        "Administration",
    ),
    "enable-jmx-monitoring": _boolean(
        "Exposes JVM monitoring data over JMX; leave off unless it is secured.",
        "Administration",
    ),
    "enable-query": _boolean(
        "Enables the legacy GameSpy query protocol for status tools.", "Network"
    ),
    "enable-rcon": _boolean(
        "Enables RCON. BLSM requires this for console and player actions.",
        "Administration",
    ),
    "enable-status": _boolean(
        "Responds to server-list status requests from Minecraft clients.", "Network"
    ),
    "enforce-secure-profile": _boolean(
        "Requires players to use Mojang-signed public keys.", "Security"
    ),
    "enforce-whitelist": _boolean(
        "Immediately disconnects players who are removed from the whitelist.", "Security"
    ),
    "entity-broadcast-range-percentage": _integer(
        "Scales how far entities are sent to clients; 100 is the normal range.",
        "Performance",
        10,
        1000,
    ),
    "force-gamemode": _boolean(
        "Forces the default game mode whenever a player joins."
    ),
    "function-permission-level": _integer(
        "Permission level used by commands inside data-pack functions.",
        "Administration",
        1,
        4,
    ),
    "gamemode": _choice(
        "Default mode assigned to new players.",
        ["survival", "creative", "adventure", "spectator"],
        "Gameplay",
    ),
    "generate-structures": _boolean(
        "Generates structures such as villages, strongholds, and monuments."
    ),
    "generator-settings": {
        "type": "string",
        "description": "Advanced JSON settings used by compatible custom world generators.",
        "category": "World",
    },
    "hardcore": _boolean(
        "Uses hard difficulty and prevents normal respawning after death."
    ),
    "hide-online-players": _boolean(
        "Hides the player sample from server-list status responses.", "Privacy"
    ),
    "initial-disabled-packs": {
        "type": "string",
        "description": "Comma-separated data packs that start disabled when a world is created.",
        "category": "World",
    },
    "initial-enabled-packs": {
        "type": "string",
        "description": "Comma-separated data packs enabled when a world is created.",
        "category": "World",
    },
    "level-name": {
        "type": "string",
        "description": "Directory name of the world. Changing it selects or creates another world.",
        "category": "World",
    },
    "level-seed": {
        "type": "string",
        "description": "Seed used only when generating a new world.",
        "category": "World",
    },
    "level-type": {
        "type": "string",
        "description": "World preset used only when generating a new world.",
        "category": "World",
    },
    "log-ips": _boolean("Includes player IP addresses in logs.", "Privacy"),
    "max-chained-neighbor-updates": _integer(
        "Limits consecutive block-neighbor updates to protect against update loops.",
        "Performance",
        -1,
    ),
    "max-players": _integer(
        "Maximum number of players allowed online at once.", "Gameplay", 1
    ),
    "max-tick-time": _integer(
        "Watchdog timeout in milliseconds; -1 disables the watchdog.",
        "Performance",
        -1,
    ),
    "max-world-size": _integer(
        "Maximum world-border radius in blocks.", "World", 1, 29999984
    ),
    "management-server-enabled": _boolean(
        "Enables Minecraft's authenticated JSON-RPC management service.",
        "Administration",
    ),
    "management-server-allowed-origins": {
        "type": "string",
        "description": (
            "Browser origins allowed to authenticate to the management service; "
            "blank disables browser-based authentication."
        ),
        "category": "Security",
    },
    "management-server-host": {
        "type": "string",
        "description": "Local address used by Minecraft's management service.",
        "category": "Network",
    },
    "management-server-port": _integer(
        "Port used by Minecraft's management service; 0 chooses an available port.",
        "Network",
        0,
        65535,
    ),
    "management-server-secret": {
        "type": "secret",
        "description": "Bearer-token secret for Minecraft's management service.",
        "category": "Administration",
    },
    "management-server-tls-enabled": _boolean(
        "Encrypts the Minecraft management service with TLS.", "Security"
    ),
    "management-server-tls-keystore": {
        "type": "string",
        "description": "Path to the PKCS12 keystore used by the management service.",
        "category": "Security",
    },
    "management-server-tls-keystore-password": {
        "type": "secret",
        "description": "Password for the management service's PKCS12 keystore.",
        "category": "Security",
    },
    "motd": {
        "type": "string",
        "description": "Message shown for this server in the multiplayer server list.",
        "category": "Presentation",
    },
    "network-compression-threshold": _integer(
        "Packets at or above this byte size are compressed; -1 disables compression.",
        "Network",
        -1,
    ),
    "online-mode": _boolean(
        "Verifies player accounts with Mojang. Disabling this permits identity spoofing.",
        "Security",
    ),
    "op-permission-level": _integer(
        "Default command permission level granted to operators.",
        "Administration",
        1,
        4,
    ),
    "pause-when-empty-seconds": _integer(
        "Pauses world ticking after the server has been empty for this many seconds.",
        "Performance",
        -1,
    ),
    "player-idle-timeout": _integer(
        "Kicks inactive players after this many minutes; 0 disables the timeout.",
        "Gameplay",
        0,
    ),
    "prevent-proxy-connections": _boolean(
        "Rejects players whose ISP/AS data differs from Mojang authentication data.",
        "Security",
    ),
    "pvp": _boolean(
        "Obsolete in 26.3. Use the minecraft:pvp game rule instead."
    ),
    "query.port": _integer(
        "UDP port used by the legacy query protocol.", "Network", 1, 65535
    ),
    "rate-limit": _integer(
        "Maximum packets per second before a client is kicked; 0 disables the limit.",
        "Network",
        0,
    ),
    "rcon.password": {
        "type": "secret",
        "description": "Password for RCON. BLSM stores the same value for console access.",
        "category": "Administration",
    },
    "rcon.port": _integer("TCP port used by RCON.", "Network", 1, 65535),
    "region-file-compression": _choice(
        "Compression algorithm used for newly written world-region data.",
        ["deflate", "lz4", "none"],
        "Performance",
    ),
    "require-resource-pack": _boolean(
        "Disconnects players who decline the configured resource pack.", "Presentation"
    ),
    "resource-pack": {
        "type": "string",
        "description": "Direct HTTPS URL of the server resource pack.",
        "category": "Presentation",
    },
    "resource-pack-id": {
        "type": "string",
        "description": "UUID identifying the configured resource pack.",
        "category": "Presentation",
    },
    "resource-pack-prompt": {
        "type": "string",
        "description": "Optional JSON text shown when asking players to install the pack.",
        "category": "Presentation",
    },
    "resource-pack-sha1": {
        "type": "string",
        "description": "SHA-1 digest used to verify the downloaded resource pack.",
        "category": "Presentation",
    },
    "server-ip": {
        "type": "string",
        "description": "Specific local address to bind; leave blank to listen on all addresses.",
        "category": "Network",
    },
    "server-port": _integer("TCP port used by Minecraft clients.", "Network", 1, 65535),
    "simulation-distance": _integer(
        "Radius in chunks where entities and game mechanics are actively ticked.",
        "Performance",
        3,
        32,
    ),
    "spawn-animals": _boolean(
        "Obsolete in 26.3; this server property was removed in Minecraft 1.21.2."
    ),
    "spawn-monsters": _boolean(
        "Obsolete in 26.3. Use the minecraft:spawn_monsters game rule instead."
    ),
    "spawn-npcs": _boolean(
        "Obsolete in 26.3; this server property was removed in Minecraft 1.21.2."
    ),
    "spawn-protection": _integer(
        "Protected radius around world spawn; 0 disables spawn protection.",
        "Gameplay",
        0,
    ),
    "status-heartbeat-interval": _integer(
        "Interval in seconds for newer status heartbeat announcements.", "Network", 0
    ),
    "sync-chunk-writes": _boolean(
        "Writes chunks synchronously for stronger durability at a performance cost.",
        "Performance",
    ),
    "text-filtering-config": {
        "type": "string",
        "description": "Provider-specific JSON configuration for chat filtering.",
        "category": "Administration",
    },
    "text-filtering-version": _integer(
        "Configuration version for the selected text-filtering provider; leave at 0 "
        "unless the provider requires another version.",
        "Administration",
        0,
    ),
    "use-native-transport": _boolean(
        "Uses optimized Linux networking when the native library is available.",
        "Performance",
    ),
    "view-distance": _integer(
        "Maximum chunk radius sent to players; larger values use more memory and bandwidth.",
        "Performance",
        3,
        32,
    ),
    "white-list": _boolean(
        "Allows only players listed in whitelist.json to join; enabled by default in 26.3.",
        "Security",
    ),
}


# Generated by Mojang's official 26.3 server JAR (SHA-1
# 33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c). Legacy entries above are retained
# so upgraded servers receive an accurate obsolete-setting explanation.
MINECRAFT_26_3_PROPERTY_NAMES = frozenset(
    {
        "accepts-transfers",
        "allow-flight",
        "broadcast-console-to-ops",
        "broadcast-rcon-to-ops",
        "bug-report-link",
        "chat-spam-threshold-seconds",
        "command-spam-threshold-seconds",
        "difficulty",
        "enable-code-of-conduct",
        "enable-jmx-monitoring",
        "enable-query",
        "enable-rcon",
        "enable-status",
        "enforce-secure-profile",
        "enforce-whitelist",
        "entity-broadcast-range-percentage",
        "force-gamemode",
        "function-permission-level",
        "gamemode",
        "generate-structures",
        "generator-settings",
        "hardcore",
        "hide-online-players",
        "initial-disabled-packs",
        "initial-enabled-packs",
        "level-name",
        "level-seed",
        "level-type",
        "log-ips",
        "management-server-allowed-origins",
        "management-server-enabled",
        "management-server-host",
        "management-server-port",
        "management-server-secret",
        "management-server-tls-enabled",
        "management-server-tls-keystore",
        "management-server-tls-keystore-password",
        "max-chained-neighbor-updates",
        "max-players",
        "max-tick-time",
        "max-world-size",
        "motd",
        "network-compression-threshold",
        "online-mode",
        "op-permission-level",
        "pause-when-empty-seconds",
        "player-idle-timeout",
        "prevent-proxy-connections",
        "query.port",
        "rate-limit",
        "rcon.password",
        "rcon.port",
        "region-file-compression",
        "require-resource-pack",
        "resource-pack",
        "resource-pack-id",
        "resource-pack-prompt",
        "resource-pack-sha1",
        "server-ip",
        "server-port",
        "simulation-distance",
        "spawn-protection",
        "status-heartbeat-interval",
        "sync-chunk-writes",
        "text-filtering-config",
        "text-filtering-version",
        "use-native-transport",
        "view-distance",
        "white-list",
    }
)


SECRET_PROPERTIES = {
    "rcon.password",
    "management-server-secret",
    "management-server-tls-keystore-password",
}
