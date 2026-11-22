from typing import Any

GAME_RULE_METADATA_VERSION = "26.3"


def _boolean(
    label: str, description: str, category: str, default: bool
) -> dict[str, Any]:
    return {
        "label": label,
        "description": description,
        "category": category,
        "type": "boolean",
        "default": default,
    }


def _integer(
    label: str,
    description: str,
    category: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "label": label,
        "description": description,
        "category": category,
        "type": "integer",
        "default": default,
        "min": minimum,
    }
    if maximum is not None:
        result["max"] = maximum
    return result


# IDs, types, categories, defaults, and ranges are taken from the registry in
# Mojang's official Minecraft Java 26.3 server JAR.
MINECRAFT_GAME_RULES: dict[str, dict[str, Any]] = {
    "minecraft:advance_time": _boolean(
        "Advance time of day",
        "Controls whether the day-night cycle advances normally.",
        "World Updates",
        True,
    ),
    "minecraft:advance_weather": _boolean(
        "Update weather",
        "Controls whether rain and thunder change over time.",
        "World Updates",
        True,
    ),
    "minecraft:allow_entering_nether_using_portals": _boolean(
        "Allow Nether portals",
        "Controls whether players may enter the Nether through portals.",
        "Miscellaneous",
        True,
    ),
    "minecraft:block_drops": _boolean(
        "Drop blocks",
        "Controls whether broken blocks drop items and experience.",
        "Drops",
        True,
    ),
    "minecraft:block_explosion_drop_decay": _boolean(
        "Block explosion drop decay",
        "Makes explosions caused by block interactions destroy some block drops.",
        "Drops",
        True,
    ),
    "minecraft:command_blocks_work": _boolean(
        "Enable command blocks",
        "Controls whether command blocks can execute commands.",
        "Miscellaneous",
        True,
    ),
    "minecraft:command_block_output": _boolean(
        "Broadcast command block output",
        "Shows command block output to server operators.",
        "Chat",
        True,
    ),
    "minecraft:drowning_damage": _boolean(
        "Deal drowning damage",
        "Controls whether players take damage when they run out of air.",
        "Player",
        True,
    ),
    "minecraft:elytra_movement_check": _boolean(
        "Check elytra movement",
        "Enables the server's movement validation while players use elytra.",
        "Player",
        True,
    ),
    "minecraft:ender_pearls_vanish_on_death": _boolean(
        "Ender pearls vanish on death",
        "Removes a player's thrown ender pearls when that player dies.",
        "Player",
        True,
    ),
    "minecraft:entity_drops": _boolean(
        "Drop entity equipment",
        "Controls drops from non-mob entities such as boats, minecarts, and item frames.",
        "Drops",
        True,
    ),
    "minecraft:fall_damage": _boolean(
        "Deal fall damage",
        "Controls whether players take damage from falling.",
        "Player",
        True,
    ),
    "minecraft:fire_damage": _boolean(
        "Deal fire damage",
        "Controls whether players take damage from fire and lava.",
        "Player",
        True,
    ),
    "minecraft:fire_spread_radius_around_player": _integer(
        "Fire spread radius",
        "Maximum distance from a player where fire spreads; 0 disables spread and -1 "
        "allows it without nearby players.",
        "World Updates",
        128,
        -1,
    ),
    "minecraft:forgive_dead_players": _boolean(
        "Forgive dead players",
        "Makes angered neutral mobs forgive a targeted player who dies nearby.",
        "Mobs",
        True,
    ),
    "minecraft:freeze_damage": _boolean(
        "Deal freeze damage",
        "Controls whether players take damage from freezing in powder snow.",
        "Player",
        True,
    ),
    "minecraft:global_sound_events": _boolean(
        "Global sound events",
        "Makes events such as boss spawns audible to every player regardless of distance.",
        "Miscellaneous",
        True,
    ),
    "minecraft:immediate_respawn": _boolean(
        "Respawn immediately",
        "Skips the death screen and respawns players immediately.",
        "Player",
        False,
    ),
    "minecraft:keep_inventory": _boolean(
        "Keep inventory after death",
        "Keeps player inventory and experience after death.",
        "Player",
        False,
    ),
    "minecraft:lava_source_conversion": _boolean(
        "Lava converts to source",
        "Allows flowing lava surrounded by two lava sources to become a source block.",
        "World Updates",
        False,
    ),
    "minecraft:limited_crafting": _boolean(
        "Require recipes for crafting",
        "Allows players to craft only recipes they have unlocked.",
        "Player",
        False,
    ),
    "minecraft:locator_bar": _boolean(
        "Enable player locator bar",
        "Shows the direction of other players in the locator bar.",
        "Player",
        True,
    ),
    "minecraft:log_admin_commands": _boolean(
        "Log administrator commands",
        "Records commands executed by administrators in the server log.",
        "Chat",
        True,
    ),
    "minecraft:max_block_modifications": _integer(
        "Command block modification limit",
        "Maximum blocks one command, such as fill or clone, may change.",
        "Miscellaneous",
        32768,
        1,
    ),
    "minecraft:max_command_forks": _integer(
        "Command context limit",
        "Maximum execution contexts commands such as execute may create.",
        "Miscellaneous",
        65536,
        0,
    ),
    "minecraft:max_command_sequence_length": _integer(
        "Command sequence length",
        "Maximum commands in a command-block chain or function sequence.",
        "Miscellaneous",
        65536,
        0,
    ),
    "minecraft:max_entity_cramming": _integer(
        "Entity cramming threshold",
        "Maximum pushable entities in one space before they take cramming damage; 0 "
        "disables the limit.",
        "Mobs",
        24,
        0,
    ),
    "minecraft:max_minecart_speed": _integer(
        "Minecart maximum speed",
        "Maximum default speed of a minecart on land when minecart improvements are enabled.",
        "Miscellaneous",
        8,
        1,
        1000,
    ),
    "minecraft:max_snow_accumulation_height": _integer(
        "Snow accumulation height",
        "Maximum number of snow layers that weather may accumulate.",
        "World Updates",
        1,
        0,
        8,
    ),
    "minecraft:mob_drops": _boolean(
        "Drop mob loot",
        "Controls item and experience drops from mobs.",
        "Drops",
        True,
    ),
    "minecraft:mob_explosion_drop_decay": _boolean(
        "Mob explosion drop decay",
        "Makes explosions caused by mobs destroy some block drops.",
        "Drops",
        True,
    ),
    "minecraft:mob_griefing": _boolean(
        "Allow destructive mob actions",
        "Allows mobs to alter the world, such as creeper damage and enderman block pickup.",
        "Mobs",
        True,
    ),
    "minecraft:natural_health_regeneration": _boolean(
        "Regenerate health",
        "Allows players to naturally regenerate health when sufficiently fed.",
        "Player",
        True,
    ),
    "minecraft:player_movement_check": _boolean(
        "Check player movement",
        "Enables the server's general player movement validation.",
        "Player",
        True,
    ),
    "minecraft:players_nether_portal_creative_delay": _integer(
        "Creative Nether portal delay",
        "Ticks a Creative player must remain in a Nether portal before traveling.",
        "Player",
        0,
        0,
    ),
    "minecraft:players_nether_portal_default_delay": _integer(
        "Survival Nether portal delay",
        "Ticks a non-Creative player must remain in a Nether portal before traveling.",
        "Player",
        80,
        0,
    ),
    "minecraft:players_sleeping_percentage": _integer(
        "Sleep percentage",
        "Percentage of eligible players who must sleep to skip the night.",
        "Player",
        100,
        0,
    ),
    "minecraft:projectiles_can_break_blocks": _boolean(
        "Projectiles can break blocks",
        "Allows impact projectiles to destroy blocks they normally can break.",
        "Drops",
        True,
    ),
    "minecraft:pvp": _boolean(
        "Enable PvP",
        "Controls whether players may damage other players.",
        "Player",
        True,
    ),
    "minecraft:raids": _boolean(
        "Enable raids",
        "Controls whether village raids may start.",
        "Mobs",
        True,
    ),
    "minecraft:random_tick_speed": _integer(
        "Random tick speed",
        "Average random block ticks per chunk section each game tick; high values can "
        "severely affect performance.",
        "World Updates",
        3,
        0,
    ),
    "minecraft:reduced_debug_info": _boolean(
        "Reduce debug information",
        "Limits the information players can see on the debug screen.",
        "Miscellaneous",
        False,
    ),
    "minecraft:respawn_radius": _integer(
        "Respawn location radius",
        "Radius around world spawn used to choose player respawn positions.",
        "Player",
        10,
        0,
    ),
    "minecraft:send_command_feedback": _boolean(
        "Send command feedback",
        "Shows command result messages to the player who executed the command.",
        "Chat",
        True,
    ),
    "minecraft:show_advancement_messages": _boolean(
        "Announce advancements",
        "Broadcasts a chat message when a player earns an advancement.",
        "Chat",
        True,
    ),
    "minecraft:show_death_messages": _boolean(
        "Show death messages",
        "Broadcasts player death messages in chat.",
        "Chat",
        True,
    ),
    "minecraft:spawner_blocks_work": _boolean(
        "Enable spawner blocks",
        "Controls whether mob spawner blocks can spawn mobs.",
        "Miscellaneous",
        True,
    ),
    "minecraft:spawn_mobs": _boolean(
        "Spawn mobs",
        "Controls natural mob spawning, including spawning during chunk generation.",
        "Spawning",
        True,
    ),
    "minecraft:spawn_monsters": _boolean(
        "Spawn monsters",
        "Controls whether hostile monsters naturally spawn.",
        "Spawning",
        True,
    ),
    "minecraft:spawn_patrols": _boolean(
        "Spawn pillager patrols",
        "Controls whether pillager patrols naturally spawn.",
        "Spawning",
        True,
    ),
    "minecraft:spawn_phantoms": _boolean(
        "Spawn phantoms",
        "Controls whether insomnia can cause phantoms to spawn.",
        "Spawning",
        True,
    ),
    "minecraft:spawn_wandering_traders": _boolean(
        "Spawn wandering traders",
        "Controls whether wandering traders naturally spawn.",
        "Spawning",
        True,
    ),
    "minecraft:spawn_wardens": _boolean(
        "Spawn wardens",
        "Controls whether sculk shriekers can summon wardens.",
        "Spawning",
        True,
    ),
    "minecraft:spectators_generate_chunks": _boolean(
        "Spectators generate chunks",
        "Allows players in Spectator mode to generate new terrain while traveling.",
        "Player",
        True,
    ),
    "minecraft:spread_vines": _boolean(
        "Vines spread",
        "Controls whether ordinary vines spread randomly to adjacent blocks.",
        "World Updates",
        True,
    ),
    "minecraft:tnt_explodes": _boolean(
        "Allow TNT explosions",
        "Controls whether TNT may be activated and explode.",
        "Miscellaneous",
        True,
    ),
    "minecraft:tnt_explosion_drop_decay": _boolean(
        "TNT explosion drop decay",
        "Makes TNT explosions destroy some block drops when enabled.",
        "Drops",
        False,
    ),
    "minecraft:universal_anger": _boolean(
        "Universal anger",
        "Makes angered neutral mobs attack any nearby player, not only their target.",
        "Mobs",
        False,
    ),
    "minecraft:water_source_conversion": _boolean(
        "Water converts to source",
        "Allows flowing water surrounded by two sources to become a source block.",
        "World Updates",
        True,
    ),
}
