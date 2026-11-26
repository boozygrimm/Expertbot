"""Side-effect-free draft production calculation; dependencies are injected."""
from copy import deepcopy


def calculate_production_loot(player: dict, now: int, *, rng, stations, storage_capacity, get_used_slots, get_world_effects) -> dict | None:
    """Calculate one production tick without modifying the supplied profile.

    Self-contained copy of the production rules that used to live in the bot's
    scheduler. The bot no longer has any alchemy tick, so this module is the
    reference now; it is not connected to production data.
    """
    inv = deepcopy(player.get("inventory", {}))
    slots = deepcopy(player.get("production_slots", []))
    world_effects = get_world_effects(player)
    storage_lvl = player.get("buildings", {}).get("storage", 0)
    max_slots = storage_capacity.get(storage_lvl, 20)
    added_count = 0
    slots_changed = False

    for slot in slots:
        s_type = slot['type']
        assigned = slot.get('assigned_at', 0)

        if assigned > 0 and (now - assigned) >= 24 * 3600:
            if not slot.get('stopped'):
                slot['worker'] = None
                slot['stopped'] = True
                slots_changed = True
            continue
        if slot.get('stopped'):
            continue

        if 'mined_items' not in slot:
            slot['mined_items'] = {}
            slots_changed = True
        possible = stations[s_type]['items']
        count = 1
        if slot.get('worker'):
            worker = slot['worker']
            if (worker == 'spirit_e' and s_type == 'mine') or \
               (worker == 'spirit_w' and s_type == 'farm') or \
               (worker == 'spirit_f' and s_type == 'factory') or \
               (worker == 'spirit_a' and s_type == 'aero'):
                count = 3
            else:
                count = 2
        if s_type == "mine":
            count += int(world_effects.get("mine_yield", 0) or 0)
        elif s_type == "farm":
            count += int(world_effects.get("farm_yield", 0) or 0)
        elif s_type == "factory":
            count += int(world_effects.get("factory_yield", 0) or 0)
        elif s_type == "aero":
            count += int(world_effects.get("aero_yield", 0) or 0)

        for _ in range(count):
            item = rng.choice(possible)
            if item not in inv and get_used_slots(inv) >= max_slots:
                break
            inv[item] = inv.get(item, 0) + 1
            added_count += 1
            slot['mined_items'][item] = slot['mined_items'].get(item, 0) + 1
            slot['total_mined'] = slot.get('total_mined', 0) + 1
            slots_changed = True
            if rng.random() < 0.02 + float(world_effects.get("rare_find_bonus", 0) or 0):
                bonus = rng.choice(["recipe_scroll", "pack_s"])
                if bonus in inv or get_used_slots(inv) < max_slots:
                    inv[bonus] = inv.get(bonus, 0) + 1

    if added_count > 0 or slots_changed:
        return {"inventory": inv, "production_slots": slots}
    return None

