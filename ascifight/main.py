from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import logging
import structlog
from structlog.contextvars import bind_contextvars

import datetime
import asyncio
import os

import game


WAIT_TIME = 5
PREGAME_WAIT = 3
LOG_DIR = "logs"

SENTINEL = object()

time_stamper = structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S.%f", utc=False)
pre_chain = [
    # Add the log level and a timestamp to the event_dict if the log entry
    # is not from structlog.
    structlog.stdlib.add_log_level,
    # Add extra attributes of LogRecord objects to the event dictionary
    # so that values passed in the extra parameter of log methods pass
    # through to log output.
    structlog.stdlib.ExtraAdder(),
    time_stamper,
]


logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.contextvars.merge_contextvars,
                    structlog.processors.JSONRenderer(sort_keys=True),
                ],
                "foreign_pre_chain": pre_chain,
            },
            "colored": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processors": [
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.contextvars.merge_contextvars,
                    structlog.processors.add_log_level,
                    structlog.processors.StackInfoRenderer(),
                    structlog.dev.set_exc_info,
                    structlog.dev.ConsoleRenderer(colors=True),
                ],
                "foreign_pre_chain": pre_chain,
            },
        },
        "handlers": {
            "default": {
                "level": "DEBUG",
                "class": "logging.StreamHandler",
                "formatter": "colored",
            },
            "file": {
                "level": "DEBUG",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": "logs/game.log",
                "backupCount": 100,
                "formatter": "plain",
            },
        },
        "loggers": {
            "": {
                "handlers": ["default", "file"],
                "level": "DEBUG",
                "propagate": True,
            },
        },
    }
)
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        time_stamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

root_logger = logging.getLogger()
logger = structlog.get_logger()


description = """
**ASCI-Fight** allows you to fight with your teammates in style.

The goal of this game is to score as many points as possible by capturing your enemies flags. Go to your enemies bases, 
grab the flags and put them on top of your own base.Any enemies that try to stop you, you can attack. Of course they will 
respawn, but the won't bother you in the next ticks.

Show your coding prowess by creating the best scripts and dominate your co-workers!

## The Game 

You control a couple of actors with different properties to rule the field of battle. Depending on their properties they 
can perform various orders for you. Once the server is up (which must be the case, because you can read this documentation)
there is a grace period before the game starts. Once it has started you can give orders for a certain time, then all orders are 
executed at once and the game is waiting for the next orders.


The _game_start_ service tells you when the next game is starting.


The game ends after a certain number of points were scored or a certain number of ticks have passed.

## Components

Whats in the game you ask? Easy!

### Actors

Actors are your minions you move over the field of battle. They have different properties like _grab_ and _attack_. The can perform _orders_ to move, attack and grab.

### Bases

Each team has one. Thats where your actor start, where your flag sits and were both your actors and your flag return when they are killed or the flag is scored by an enemy team.

### Flags

There is a flag in each base. Your actor can grab it, pass it to another actor, throw it down or capture it in your own base to score!

### Walls

You, and your actors, cant walk through these!

## Orders

You can perform a couple of orders do reach your goals of co-worker domination. Orders are executed in the order (no pun intended) 
below. 
But beware, each _actor_ can only carry out each order only once per game tick.

### Move Order

With a move order you can move around any of your _actors_, by exactly one field in any non-diagonal direction. 

It is not allowed to step on fields:

* **contain another actor**
* **contain a base**
* **contain a wall field**

If an _actor_ moves over the flag of its own team, the flag is returned to its base!

### GrabPut Order

If an _actor_ does not have the flag, it can grab it with this order. Give it a direction from its current position and it will try to grab
the _flag_ from the target field. 

If an _actor_ does have the flag it can put it into a target field. This target field can be empty or contain an _actor_, but not a wall.
If the target field contains an _actor_ that can not carry the flag (_grab_ property is zero) this will not work. If an _actor_ puts a an enemy flag
on its on base, while the flag is at home, you **score**!


GrabPut actions only have a certain probability to work. If the _grab_ property of an _actor_ is smaller than 1, grabbing or putting might not succeed always.


Only _actors_ with a non-zero _grab_ property can _grabput_.

### Attack Order

With attack orders you can force other actors, even your own, to respawn near their base. Just hit them and they are gone.


Attack actions only have a certain probability to work. If the _attack_ property of an _actor_ is smaller than 1, attacking might not succeed always.

Only _actors_ with a non-zero _attack_ property can _attack_.

## States

To act you need to know things. ASCI fight is a perfect information game. So you can directly see what you need to do and what your actions have caused.

### Game State

This gets you the current state of the game. The position of each game component is something you can find here. Also other information like the current tick and such.

### Game Rules

This section is static per game and tells you what each actor can do, if the flag needs to be at home to score, what the maximum score or tick number is and other static information.

### Game Timing

The current tick and when the next tick executes both on absolute time and time-deltas. This is more lightweight than the _Game State_ an can be queried often. 

### Game Start

If the game has not started yet, this service tells you when it will.

### Log Files

This service tells you which log files are available. 'game.log' is always the log file of the current game. Others get a number attached.

You can fetch log files through the '/logs/[filename]' endpoint.

"""

tags_metadata = [
    {
        "name": "orders",
        "description": "Operations to give orders to your actors.",
    },
    {
        "name": "states",
        "description": "Operations to get state information about the game.",
    },
]

app = FastAPI(
    openapi_tags=tags_metadata,
    title="A Social, Community Increasing - Fight",
    description=description,
    version="0.1",
    contact={
        "name": "Ralf Kelzenberg",
        "url": "http://vodafone.com",
        "email": "Ralf.Kelzenberg@vodafone.com",
    },
)
app.mount("/logs", StaticFiles(directory=LOG_DIR), name="logs")

command_queue = asyncio.Queue()


class ActorDescriptions(BaseModel):
    team: str = Field(description="The name of the actor's team.")
    type: str = Field(description="The type of the actor determining its capabilities.")
    ident: int = Field(description="The identity number specific to the team.")
    coordinates: game.Coordinates


class StateResponse(BaseModel):
    teams: list[str] = Field(
        description="A list of all teams in the game. This is also the order of flags and bases."
    )
    actors: list[ActorDescriptions] = Field(
        description="A list of all actors in the game."
    )
    flags: list[game.Coordinates] = Field(
        description="A list of all flags in the game. The flags are ordered according to the teams they belong to."
    )
    bases: list[game.Coordinates] = Field(
        description="A list of all bases in the game. The bases are ordered according to the teams they belong to."
    )
    walls: list[game.Coordinates] = Field(
        description="A list of all walls in the game. Actors can not enter wall fields."
    )
    scores: list[int] = Field(
        description="A list of the current scores. The scored are ordered according to the teams they belong to."
    )
    tick: int = Field(description="The last game tick.")
    time_of_next_execution: datetime.datetime = Field(
        description="The time of next execution."
    )


class TimingResponse(BaseModel):
    tick: int = Field(description="The last game tick.")
    time_to_next_execution: datetime.timedelta = Field(
        description="The time to next execution in seconds."
    )
    time_of_next_execution: datetime.datetime = Field(
        description="The time of next execution."
    )


class ActorProperty(BaseModel):
    type: str
    grab: float = Field(
        description="The probability to successfully grab or put the flag. "
        "An actor with 0 can not carry the flag. Not even when it is given to it.",
    )
    attack: float = Field(
        description="The probability to successfully attack. An actor with 0 can not attack.",
    )


class RulesResponse(BaseModel):
    map_size: int = Field(
        default=game.MAP_SIZE, description="The length of the game board in x and y."
    )
    max_ticks: int = Field(
        default=game.MAX_TICKS,
        description="TThe maximum number of ticks the game will last.",
    )
    max_score: int = Field(
        default=game.MAX_SCORE,
        description="The maximum score that will force the game to end.",
    )
    home_Flag_not_required: bool = Field(
        default=game.HOME_FLAG_REQUIRED,
        description="Is the flag required to be at home to score? ",
    )
    actor_types: list[ActorProperty]


@app.post("/move_order", tags=["orders"])
async def move_order(order: game.MoveOrder):
    """Move an actor into a direction. Moving over your own flag return it to the base."""
    command_queue.put_nowait(order)
    return {"message": "Move order added."}


@app.post("/attack_order", tags=["orders"])
async def attack_order(order: game.AttackOrder):
    """Only actors with the attack property can attack."""
    command_queue.put_nowait(order)
    return {"message": "Attack order added."}


@app.post("/grabput_order", tags=["orders"])
async def grabput_order(order: game.GrabPutOrder):
    """If the actor has a flag it puts it, even to another actor that can carry it. If it doesn't have a flag, it grabs it, even from another actor."""
    command_queue.put_nowait(order)
    return {"message": "Grabput order added."}


@app.get("/game_state", tags=["states"])
async def get_game_state() -> StateResponse:
    """Get the current state of the game including locations of all actors, flags, bases and walls."""
    return StateResponse(
        teams=my_game.teams,
        actors=[
            ActorDescriptions(
                team=actor.team.name,
                type=actor.type,
                ident=actor.ident,
                coordinates=coordinates,
            )
            for actor, coordinates in my_game.board.actors_coordinates.items()
        ],
        flags=list(my_game.board.flags_coordinates.values()),
        bases=list(my_game.board.bases_coordinates.values()),
        walls=list(my_game.board.walls_coordinates),
        scores=list(my_game.scores.values()),
        tick=my_game.tick,
        time_of_next_execution=my_game.time_of_next_execution,
    )


@app.get("/game_rules", tags=["states"])
async def get_game_rules() -> RulesResponse:
    """Get the current rules and actor properties."""
    actor_types = [
        ActorProperty(type=actor.type, grab=actor.grab, attack=actor.attack)
        for actor in my_game.actors_of_team[my_game.teams[0]]
    ]
    return RulesResponse(actor_types=actor_types)


@app.get("/timing", tags=["states"])
async def get_timing() -> TimingResponse:
    """Get the current tick and time of next execution."""
    return TimingResponse(
        tick=my_game.tick,
        time_to_next_execution=my_game.time_to_next_execution,
        time_of_next_execution=my_game.time_of_next_execution,
    )


@app.get("/game_start", tags=["states"])
async def get_game_start() -> int:
    """Return the seconds till the game will start."""
    return my_game.pregame_wait


@app.get("/log_files", tags=["states"])
async def get_log_files() -> list[str]:
    """Get all log files accessible through /logs/[filename]"""
    return os.listdir(LOG_DIR)


teams = [
    game.Team(name="S", password="1", number=0),
    game.Team(name="G", password="1", number=1),
    game.Team(name="M", password="1", number=2),
]

my_game: game.Game


async def routine():
    while True:
        await single_game()


async def single_game():
    global my_game
    for handler in root_logger.handlers:
        if handler.__class__.__name__ == "RotatingFileHandler":
            handler.doRollover()
    my_game = game.Game(
        teams=teams,
        pregame_wait=PREGAME_WAIT,
        board=game.Board(walls=0),
        actors=game.InitialActorsList(actors=[game.Generalist]),
    )

    # my_game = Game(teams=teams, pregame_wait = PREGAME_WAIT,
    #     board=Board(walls=0),
    #     actors=InitialActorsList(actors=[game.Generalist, game.Generalist, game.Generalist]),
    # )

    # my_game = Game(teams=teams, pregame_wait = PREGAME_WAIT,
    #     board=Board(walls=0),
    #     actors=InitialActorsList(actors=[game.Runner, game.Attacker, game.Attacker]),
    # )

    # my_game = Game(teams=teams, pregame_wait = PREGAME_WAIT,
    #     board=Board(walls=10),
    #     actors=InitialActorsList(actors=[game.Runner, game.Attacker, game.Attacker]),
    # )

    # my_game = Game(teams=teams, pregame_wait = PREGAME_WAIT,
    #     board=Board(walls=10),
    #     actors=InitialActorsList(actors=[game.Runner, game.Attacker, game.Attacker, game.Blocker, game.Blocker]),
    # )
    logger.info("Initiating game.")
    my_game.initiate_game()

    logger.info("Starting pre-game.")
    while my_game.pregame_wait > 0:
        await asyncio.sleep(1)
        my_game.pregame_wait -= 1

    while not my_game.check_game_end():
        await command_queue.put(SENTINEL)

        commands = await get_all_queue_items(command_queue)

        bind_contextvars(tick=my_game.tick)
        os.system("cls" if os.name == "nt" else "clear")

        print(my_game.scoreboard())
        print(my_game.board.image())

        logger.info("Starting tick execution.")
        my_game.execute_game_step(commands)

        logger.info("Waiting for game commands.")
        my_game.time_of_next_execution = datetime.datetime.now() + datetime.timedelta(
            0, WAIT_TIME
        )
        next_execution = my_game.time_of_next_execution
        logger.info(f"Time of next execution: {next_execution}")

        await asyncio.sleep(WAIT_TIME)
    my_game.end_game()
    os.system("cls" if os.name == "nt" else "clear")
    print(my_game.scoreboard())
    print(my_game.board.image())


async def get_all_queue_items(queue):
    items = []
    item = await queue.get()
    while item is not SENTINEL:
        items.append(item)
        queue.task_done()
        item = await queue.get()
    queue.task_done()
    return items


async def ai_generator():
    await asyncio.sleep(1)
    while True:
        await asyncio.sleep(5)
        await command_queue.put(
            game.MoveOrder(team="S", password="1", actor=0, direction="down")
        )
        await asyncio.sleep(5)
        await command_queue.put(
            game.MoveOrder(team="S", password="1", actor=0, direction="right")
        )


@app.on_event("startup")
async def startup():
    asyncio.create_task(routine())
    # asyncio.create_task(ai_generator())
