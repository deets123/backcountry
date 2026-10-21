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
