from backcountry.chat.grounding import CANT_ANSWER, check_reply

FACTS = """Route (candidate 1 of 3, loop): 19.02 mi, gain 2,023 ft, loss 2,023 ft, road 0.81 mi.
Trails in order: Bear Rocks Trail > Raven Ridge Trail > Red Creek Trail > Dolly Sods Road.
Day 1: Start to Camp 1, 6.34 mi, gain 784 ft, loss 622 ft.
Day 3: Camp 2 to End, 6.34 mi, gain 908 ft, loss 422 ft.
Camp 2: mile 12.68, on Red Creek Trail; nearest water: Stream crossing: Stonecoal Run, 4,251 ft away (presence unknown)."""


def test_numbers_must_come_from_the_facts_at_the_replys_precision():
    assert check_reply("Day 3 is hardest, with 908 ft of climbing over 6.3 mi.", FACTS) == (True, [])
    assert check_reply("The loop is about 19 miles with 2,023 ft of gain.", FACTS)[0]
    ok, reasons = check_reply("Day 3 climbs 1,200 ft.", FACTS)
    assert not ok and reasons == ["unsupported number '1,200'"]


def test_other_units_and_unknown_numbers_are_withheld():
    assert not check_reply("That's about 30.6 km in total.", FACTS)[0]
    assert not check_reply("Camp 2 is 1296 m from the stream.", FACTS)[0]
    assert not check_reply("Allow 9 hours for day 3.", FACTS)[0]


def test_banned_claims_are_withheld_unless_said_as_unknown():
    for bad in ["This route is safe for beginners.", "Water is available at Stonecoal Run.", "There's reliable water at camp 2.",
                "Camping is legal anywhere here.", "No permit is needed.", "The trail is in good condition.", "Red Creek Trail is open."]:
        ok, reasons = check_reply(bad, FACTS)
        assert not ok, bad
    assert check_reply("Whether water is there is unknown; this app hasn't checked.", FACTS)[0]
    assert check_reply("I can't tell you whether it's safe: conditions aren't checked by this app.", FACTS)[0]
    assert check_reply("Permits are not checked by this tool.", FACTS)[0]


def test_trail_and_place_names_must_be_in_the_facts():
    assert check_reply("Camp 2 is on Red Creek Trail.", FACTS)[0]
    assert check_reply("The Red Creek Trail section is the longest.", FACTS)[0]
    ok, reasons = check_reply("Take the Moon Valley Trail instead.", FACTS)
    assert not ok and reasons == ["names 'Moon Valley Trail', which isn't in this trip's data"]


def test_the_fallback_wording():
    assert CANT_ANSWER == "I can't answer that from this trip's data."


def test_not_confirmed_and_whether_wording_counts_as_unknown():
    reply = ("Water presence along the route is unknown. Stream crossings are noted, but whether water is present, "
             "flowing, or drinkable is not confirmed.")
    assert check_reply(reply, FACTS) == (True, [])
    assert not check_reply("Water is present at Stonecoal Run.", FACTS)[0]


def test_unit_conversions_are_withheld_even_when_the_bare_number_is_in_the_facts():
    for bad in ["The loop is 19 km long.", "Camp 2 is about 1 km from Stonecoal Run.", "Day 1 climbs 784 m.",
                "Day 1 is 6.34 kilometers."]:
        ok, reasons = check_reply(bad, FACTS)
        assert not ok and "uses other units" in reasons, bad
    assert check_reply("Day 1 is 6.34 mi with 784 ft of gain.", FACTS)[0]


def test_positive_permit_legality_and_water_claims_are_withheld():
    for bad in ["You need a permit for Dolly Sods.", "A permit is required here.", "Camping is allowed anywhere here.",
                "There is water at Stonecoal Run.", "Camp 2 has water nearby.", "There is plenty of water along Red Creek Trail.",
                "You will find water at Stonecoal Run."]:
        assert not check_reply(bad, FACTS)[0], bad
    assert check_reply("This lowers the most miles allowed in a day.", FACTS)[0]


def test_the_unknown_exemption_covers_only_its_own_clause():
    assert not check_reply("I cannot say whether it is crowded, but the water is reliable and the route is safe.", FACTS)[0]
    assert check_reply("Whether there is water at Stonecoal Run is unknown.", FACTS)[0]


def test_settings_wording_and_conditional_permit_questions_are_not_claims():
    assert check_reply("It uses 0.81 mi of roads, which is acceptable since roads are allowed.", FACTS)[0]
    assert check_reply("Contact the relevant authority to determine if a permit is required for this trip.", FACTS)[0]
    assert check_reply("Ask whether a permit is needed.", FACTS)[0]
    for bad in ["Camping is allowed anywhere here.", "Fires are allowed at camp 2.", "Dogs are allowed.", "A permit is required."]:
        assert not check_reply(bad, FACTS)[0], bad


LEVEL_FACTS = FACTS + """
Day 1: Start to Camp 1, 6.34 mi, gain 784 ft, loss 622 ft, level moderate.
Day 2: Camp 1 to Camp 2, 6.34 mi, gain 331 ft, loss 300 ft, level hard (steep).
Day 3: Camp 2 to End, 6.34 mi, gain 908 ft, loss 422 ft, level unknown (no elevation data).
Constraint not met: Day 2 came out hard, not easy"""


def test_level_claims_must_match_the_computed_levels():
    ok, reasons = check_reply("Day 2 is easy.", LEVEL_FACTS)
    assert not ok and reasons == ["says day 2 is easy, but it is hard"]
    assert check_reply("Day 2 is hard because it's steep.", LEVEL_FACTS) == (True, [])
    assert check_reply("Day 2 came out hard, not easy.", LEVEL_FACTS) == (True, [])
    assert check_reply("Day 1 is moderate and day 2 is hard.", LEVEL_FACTS) == (True, [])
    assert not check_reply("Day 1 is moderate and day 2 is moderate.", LEVEL_FACTS)[0]
    assert check_reply("Day 3's level is unknown.", LEVEL_FACTS)[0]
    assert check_reply("Day 3 is hard to say.", LEVEL_FACTS)[0]  # "hard to" isn't a level
    ok, reasons = check_reply("Day 3 is strenuous.", LEVEL_FACTS)
    assert not ok and reasons == ["says day 3 is strenuous, but its level is unknown"]
