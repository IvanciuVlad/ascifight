from pydantic import ValidationError
import toml
import structlog

import random
import enum
import itertools

import ascifight.board_data as board_data
import ascifight.util as util

with open("config.toml", mode="r") as fp:
    config = toml.load(fp)


class Directions(str, enum.Enum):
    left = "left"
    right = "right"
    down = "down"
    up = "up"


class BoardActions:
    def __init__(
        self,
        game_board_data: board_data.BoardData,
    ):
        self._logger = structlog.get_logger()
        self.board_data = game_board_data

    def calc_target_coordinates(
        self, actor: board_data.Actor, direction: Directions
    ) -> board_data.Coordinates:
        coordinates = self.board_data.actors_coordinates[actor]
        new_coordinates = board_data.Coordinates(x=coordinates.x, y=coordinates.y)
        if direction == direction.right:
            new_coordinates.x = min(coordinates.x + 1, self.board_data.map_size - 1)
        if direction == direction.left:
            new_coordinates.x = max(coordinates.x - 1, 0)
        if direction == direction.up:
            new_coordinates.y = min(coordinates.y + 1, self.board_data.map_size - 1)
        if direction == direction.down:
            new_coordinates.y = max(coordinates.y - 1, 0)
        return new_coordinates

    def move(
        self, actor: board_data.Actor, direction: Directions
    ) -> tuple[bool, None | board_data.Team]:
        team_that_scored = None
        new_coordinates = self.calc_target_coordinates(actor, direction)
        moved = self._try_put_actor(actor, new_coordinates)
        if moved:
            team_that_scored = self._check_flag_return_conditions(actor)
        return moved, team_that_scored

    def attack(self, actor: board_data.Actor, direction: Directions) -> bool:
        attacked = False
        if not actor.attack:
            self._logger.warning(f"{actor} can not attack.")
        else:
            target_coordinates = self.calc_target_coordinates(actor, direction)
            target = self.board_data.coordinates_actors.get(target_coordinates)
            if target is None:
                self._logger.warning(
                    f"No target on target coordinates {target_coordinates}."
                )
            else:
                attack_successful = random.random() < actor.attack
                if not attack_successful:
                    self._logger.info(f"{actor} attacked and missed {target}.")
                else:
                    self._logger.info(f"{actor} attacked and hit {target}.")
                    self._respawn(target)
                    attacked = True
        return attacked

    def grabput_flag(
        self, actor: board_data.Actor, direction: Directions
    ) -> tuple[bool, None | board_data.Team]:
        team_that_scored = None
        target_coordinates = self.calc_target_coordinates(actor, direction)

        grab_successful = random.random() < actor.grab
        target_actor = self.board_data.coordinates_actors.get(target_coordinates)
        already_grabbed = False
        flag: board_data.Flag | None

        if actor.flag is not None:
            flag = actor.flag

            if target_actor is not None:
                if not target_actor.grab:
                    self._logger.warning(
                        f"{actor} can not hand the flag to actor {target_actor}. Can not have the flag."
                    )

                elif target_actor.flag is not None:
                    self._logger.warning(
                        f"{actor} can not hand the flag to actor {target_actor}. Target already has a flag."
                    )

                else:
                    self.board_data.flags_coordinates[flag] = target_coordinates
                    actor.flag = None
                    target_actor.flag = flag
                    already_grabbed = True
                    self._logger.info(f"{actor} handed the flag to {target_actor}.")
                    team_that_scored = self._check_flag_return_conditions(target_actor)

            # no target actor, means empty field, wall or base (even a flag???)
            else:
                if target_coordinates in self.board_data.walls_coordinates:
                    self._logger.warning(f"{actor} can not hand the flag to a wall.")

                # the flag was put on the field (maybe a base)
                else:
                    self.board_data.flags_coordinates[flag] = target_coordinates
                    actor.flag = None
                    already_grabbed = True
                    self._logger.info(
                        f"{actor} put the flag to coordinates {target_coordinates}."
                    )
                    team_that_scored = self._check_score_conditions(flag)

        # the actor does not have the flag
        else:
            flag = self.board_data.coordinates_flags.get(target_coordinates)
            if flag is None:
                self._logger.warning(f"No flag at coordinates {target_coordinates}.")
            else:
                if grab_successful:
                    self.board_data.flags_coordinates[
                        flag
                    ] = self.board_data.actors_coordinates[actor]
                    actor.flag = flag
                    already_grabbed = True

                    # and remove it from the target actor if there is one
                    if target_actor is not None:
                        target_actor.flag = None
                        self._logger.info(
                            f"{actor} grabbed the flag of {flag.team} from {target_actor}."
                        )
                    else:
                        self._logger.info(f"{actor} grabbed the flag of {flag.team}.")
                    team_that_scored = self._check_flag_return_conditions(actor=actor)
                else:
                    self._logger.info(f"{actor} grabbed and missed the flag.")

        return already_grabbed, team_that_scored

    def _check_score_conditions(
        self, flag_to_score: board_data.Flag | None = None
    ) -> board_data.Team | None:
        team_that_scored = None
        flags = (
            [flag_to_score]
            if flag_to_score
            else [flag for flag in self.board_data.flags_coordinates.keys()]
        )
        for flag_to_score in flags:
            score_flag_coordinates = self.board_data.flags_coordinates[flag_to_score]
            base_at_flag_coordinates = self.board_data.coordinates_bases.get(
                score_flag_coordinates
            )
            # if the flag is an enemy flag and owner flag is also there
            if (
                # flag is on a base
                base_at_flag_coordinates is not None
                # flag is not on it own base
                and (flag_to_score.team != base_at_flag_coordinates.team)
            ):
                scoring_team = base_at_flag_coordinates.team
                # own flag is at base or this is not required
                if (
                    self.board_data.flags_coordinates[
                        board_data.Flag(team=scoring_team)
                    ]
                    == self.board_data.bases_coordinates[
                        board_data.Base(team=scoring_team)
                    ]
                ) or (not config["game"]["home_flag_required"]):
                    self._logger.info(
                        f"{scoring_team} scored {flag_to_score.team} flag!"
                    )
                    team_that_scored = scoring_team
                    # return the flag to the base it belongs to

                else:
                    self._logger.warning("Can not score, flag not at home.")
        return team_that_scored

    def _respawn(self, actor: board_data.Actor) -> None:
        base_coordinates = self.board_data.bases_coordinates[
            board_data.Base(team=actor.team)
        ]
        possible_spawn_points = []
        for x in range(base_coordinates.x - 2, base_coordinates.x + 3):
            for y in range(base_coordinates.y - 2, base_coordinates.y + 3):
                try:
                    possible_spawn_points.append(board_data.Coordinates(x=x, y=y))
                # ignore impossible positions
                except ValidationError:
                    pass
        actor_positions = list(self.board_data.actors_coordinates.values())
        flag_positions = list(self.board_data.flags_coordinates.values())
        base_positions = list(self.board_data.bases_coordinates.values())
        walls_positions = list(self.board_data.walls_coordinates)
        forbidden_positions = set(
            flag_positions + actor_positions + base_positions + walls_positions
        )
        self._place_actor_in_area(actor, possible_spawn_points, forbidden_positions)

    def _return_flag_to_base(self, flag: board_data.Flag) -> None:
        self.board_data.flags_coordinates[flag] = self.board_data.bases_coordinates[
            board_data.Base(team=flag.team)
        ]

    def _check_flag_return_conditions(
        self, actor: board_data.Actor
    ) -> board_data.Team | None:
        team_that_scored = None
        coordinates = self.board_data.actors_coordinates[actor]
        if coordinates in self.board_data.flags_coordinates.values():
            flag = self.board_data.coordinates_flags[coordinates]
            # if flag is own flag, return it to base
            if flag.team == actor.team:
                self._return_flag_to_base(flag)
                team_that_scored = self._check_score_conditions()
                if actor.flag:
                    actor.flag = None
        return team_that_scored

    def _place_actor_in_area(
        self,
        actor: board_data.Actor,
        possible_spawn_points: list[board_data.Coordinates],
        forbidden_positions: set[board_data.Coordinates],
    ) -> None:
        allowed_positions = set(possible_spawn_points) - set(forbidden_positions)
        target_coordinates = random.choice(list(allowed_positions))
        if actor.flag is not None:
            self._logger.info(f"{actor} dropped flag {actor.flag}.")
            actor.flag = None
        self.board_data.actors_coordinates[actor] = target_coordinates
        self._logger.info(f"{actor} respawned to coordinates {target_coordinates}.")

    def _get_area_positions(
        self, center: board_data.Coordinates, distance: int
    ) -> list[board_data.Coordinates]:
        positions: list[board_data.Coordinates] = []
        for x in range(center.x - distance, center.x + distance):
            for y in range(center.y - distance, center.y + distance):
                try:
                    positions.append(board_data.Coordinates(x=x, y=y))
                    # ignore forbidden space out of bounds
                except ValidationError:
                    pass
        return positions

    def _try_put_actor(
        self, actor: board_data.Actor, new_coordinates: board_data.Coordinates
    ) -> bool:
        coordinates = self.board_data.actors_coordinates[actor]
        moved = False

        if coordinates == new_coordinates:
            self._logger.warning(
                f"{actor} did not move. Target field is out of bounds."
            )
        elif self.board_data.coordinates_actors.get(new_coordinates) is not None:
            self._logger.warning(f"{actor} did not move. Target field is occupied.")
        elif self.board_data.coordinates_bases.get(new_coordinates) is not None:
            self._logger.warning(f"{actor} did not move. Target field is abase.")
        elif new_coordinates in self.board_data.walls_coordinates:
            self._logger.warning(f"{actor} did not move. Target field is a wall.")
        else:
            self.board_data.actors_coordinates[actor] = new_coordinates
            moved = True
            # move flag if actor has it
            if actor.flag is not None:
                flag = actor.flag
                self.board_data.flags_coordinates[flag] = new_coordinates

            self._logger.info(f"{actor} moved from {coordinates} to {new_coordinates}")

        return moved
