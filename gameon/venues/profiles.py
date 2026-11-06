"""Optional local preference files, deliberately separate from hosted player records."""
import argparse
import json
from pathlib import Path
from .contracts import GroupProfile, PlayerPreference, VenuePreferences


def load_preferences(group_file=None, player_file=None):
    group = GroupProfile.model_validate_json(Path(group_file).read_text(encoding='utf-8')) if group_file else GroupProfile()
    players = tuple(PlayerPreference.model_validate_json(json.dumps(p)) for p in
                    json.loads(Path(player_file).read_text(encoding='utf-8'))) if player_file else ()
    return VenuePreferences(group=group, players=players)


def main():
    parser = argparse.ArgumentParser(description='Create optional local venue preferences; existing files are never overwritten.')
    sub = parser.add_subparsers(dest='kind', required=True)
    group = sub.add_parser('group')
    group.add_argument('--group-id', required=True)
    group.add_argument('--venue', action='append', default=[])
    group.add_argument('--restrict', action='store_true')
    group.add_argument('--indoor-only', action='store_true')
    group.add_argument('--max-distance-km', type=float, default=80.0)
    player = sub.add_parser('players')
    player.add_argument('--preference', action='append', required=True, help='PLAYER_ID=VENUE_ID,VENUE_ID (repeat per player)')
    for command in (group, player):
        command.add_argument('--file', type=Path, required=True, help='Prefer an ignored .local/*.json file')
    args = parser.parse_args()
    try:
        if args.kind == 'group':
            content = GroupProfile(group_id=args.group_id, preferred_venue_ids=tuple(args.venue),
                restrict_to_preferences=args.restrict, indoor_only=args.indoor_only,
                max_individual_distance_km=args.max_distance_km).model_dump_json(indent=2)
        else:
            players = tuple(PlayerPreference(player_id=p.split('=', 1)[0],
                            venue_ids=tuple(p.split('=', 1)[1].split(','))) for p in args.preference)
            validated = VenuePreferences(players=players)
            content = json.dumps([p.model_dump(mode='json') for p in validated.players], indent=2)
        args.file.parent.mkdir(parents=True, exist_ok=True)
        with args.file.open('x', encoding='utf-8') as destination:
            destination.write(content + '\n')
        print('Saved local preferences. Reuse group files; player IDs must belong to each selected game.')
        return 0
    except (ValueError, IndexError, OSError):
        print('Preferences not saved: check input and use a new filename; existing files are preserved.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
