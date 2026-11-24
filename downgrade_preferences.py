"""Soft preferences; never relax price, margin or demand requirements."""
SHORTLIST = {
    'Bunny Swirl Neon Ride', 'Reindeer Neon Ride', 'Reindeer Neon Fly Ride',
    'Fallow Deer Fly Ride', 'Pupcicle Neon Ride', 'Arctic Dusk Dragon Fly Ride',
    'Honey Badger', 'Christmas Pudding Pup Fly Ride',
}

def preferred(bot, name):
    return bot.canonical_slug(name) in {bot.canonical_slug(n) for n in SHORTLIST}


def rank_options(bot, options):
    for option in options:
        option['preferred_pets'] = list(dict.fromkeys(n for n in option['receive'] if preferred(bot, n)))
        option['preferred'] = bool(option['preferred_pets'])
    # Demand comes first. A 20% ranking bonus is not an extra financial return.
    options.sort(key=lambda o: (not o['liquid'], -o['extra_net'] * (1.2 if o['preferred'] else 1), -o['extra_net']))
