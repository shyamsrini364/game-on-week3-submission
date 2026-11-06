"""Try: python -m courtbot.demo"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .models import GameSession, Player, TimeSlot
from .scheduling import find_common_slots

melbourne = ZoneInfo("Australia/Melbourne")

def create_example_game() -> GameSession:
    saturday = datetime(2026, 11, 7, 10, 0, tzinfo=melbourne)
    sunday = datetime(2026, 11, 8, 9, 0, tzinfo=melbourne)
    game = GameSession(
        id="saturday-smash", sport="pickleball", min_players=4,
        slots=(TimeSlot("sat-10", saturday, saturday + timedelta(minutes=90)),
               TimeSlot("sun-9", sunday, sunday + timedelta(minutes=90)))
    )
    for player in (Player("p1", "Alex", "Lyndhurst"),
                   Player("p2", "Sam", "Berwick"),
                   Player("p3", "Jess", "Clayton"),
                   Player("p4", "Priya", "Cranbourne")):
        game.add_player(player)
    game.submit_availability("p1", {"sat-10", "sun-9"})
    game.submit_availability("p2", {"sat-10"})
    game.submit_availability("p3", {"sat-10", "sun-9"})
    game.submit_availability("p4", {"sat-10"})
    return game


def main() -> None:
    game = create_example_game()
    result = find_common_slots(game)
    print(f"CourtBot | {game.id} | {result.status}")
    for match in result.matches:
        when = match.slot.starts_at.strftime("%a %d %b, %I:%M %p %Z")
        print(f"  {when}: {match.available_count}/{len(game.players)} available")
    print(result.explanation)
    print("Next: an agent will use this result to decide whether to search courts or ask for more times.")

if __name__ == "__main__":
    main()
