import bot

pet_names = ["Parrot Fly Ride", "Fairy Bat Dragon Neon Fly Ride"]

for name in pet_names:
    base, pumping, fly, ride = bot.parse_variant_components(name)
    base = bot.SLUG_ALIASES.get(base, base)
    
    print(f"\n{"="*50}")
    print(f"PET: {name}")
    print(f"Parsed Base: {base} | Pumping: {pumping} | Fly: {fly} | Ride: {ride}")
    
    # What did the catalog find?
    mult = bot.variant_multiplier(name)
    r, mult = bot._lookup(name)
    
    if r:
        print(f"Found in catalog: {r['realName']} | {r['pumping']} | F:{r['flyable']} R:{r['rideable']}")
        print(f"Raw sellable_at: ${r['sellable_at']}")
        print(f"Raw price_low:   ${r['price_low']}")
        print(f"Amvgg Variant Multiplier: {mult}x")
        print(f"Final USD (after tax): ${bot.pet_to_usd(name)}")
    else:
        print("❌ NOT FOUND IN CATALOG. Falling back to base.")
        print(f"Base frost: {bot.pet_to_frost(name)}")
        print(f"Amvgg Variant Multiplier: {mult}x")
        print(f"Final USD (after tax): ${bot.pet_to_usd(name)}")