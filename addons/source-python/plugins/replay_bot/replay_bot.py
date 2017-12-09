# TODO:
# - Capture/replay client commands
# - Capture/replay player model

# ==============================================================================
# >> IMPORTS
# ==============================================================================
# Python
import time

from enum import IntEnum

# Source.Python
from core import SOURCE_ENGINE_BRANCH
from entities.helpers import index_from_edict

from players.bots import bot_manager
from players.bots import BotCmd
from players.dictionary import PlayerDictionary
from players.entity import Player as SPPlayer

from engines.server import server
from engines.server import global_vars

from listeners import OnTick
from listeners import OnEntityDeleted


# ==============================================================================
# >> CLASSES
# ==============================================================================
class Snapshot(object):
    """This class is used to store properties/actions of a player at a single
    game frame."""

    def __init__(self, origin, angle, velocity, bcmd):
        """Initialize the snapshot.

        :param Vector origin:
            The current location of the player.
        :param QAngle angle:
            The current angle of the player.
        :param Vector velocity:
            The current velocity of the player.
        :param BotCmd bcmd:
            The bot command of the player.
        """
        self.origin = origin
        self.angle = angle
        self.velocity = velocity
        self.bcmd = bcmd

    def replay_bcmd(self, controller):
        """Replay the stored action using the given player."""
        controller.run_player_move(self.bcmd)

    def replay_location(self, player):
        """Correct the player's location."""
        player.teleport(
            origin=self.origin,
            angle=self.angle,
            velocity=self.velocity)


class PlayerData(object):
    """A class to store player/client information."""

    def __init__(self, name, steamid, team):
        """Initialize the instance.

        :param str name:
            Name of the player.
        :param str steamid:
            SteamID of the player.
        :param int team:
            Team index of the player.
        """
        self.name = name
        self.steamid = steamid
        self.team = team


class Recording(list):
    """This class represents a recording.

    A recording is a sequence of snapshots and some meta information.
    """

    def __init__(self, player):
        """Initialize the recording.

        :param players.entity.Player player:
            The player that gets recorded. This instance is only used during
            the initialization of this class.
        """
        self.creation_time = time.time()
        self.game = SOURCE_ENGINE_BRANCH
        self.map_name = global_vars.map_name
        self.tick_interval = server.tick_interval
        self.player = PlayerData(player.name, player.steamid, player.team)

    def remove(self):
        """Remove the recording from the recording database."""
        recording_mgr.remove(self)

    def is_playable(self):
        """Return whether the recording is playable.

        A recording is playable if it has at least one snapshot.

        :rtype: bool
        """
        return len(self) > 0

    def play(self, replay_bot_name=None):
        """Get or create a :class:`Player` instance and start playing the
        recording.

        :param str replay_bot_name:
            The name that should be used for the replay bot. If ``None`` a name
            is generated.
        :rtype: Player
        """
        player = recording_mgr.get_player(self, replay_bot_name)
        player.start()
        return player

    @property
    def duration(self):
        """Return the duration of the recording in seconds.

        :rtype: float
        """
        return len(self) * self.tick_interval

    def add_snapshot(self, player):
        """Create a snapshot for the given player and add it to this recording.

        :param players.entity.Player player:
            The player for which a snapshot should be created.
        """
        self.append(self.create_snapshot(player))

    @classmethod
    def create_snapshot(cls, player):
        """Create a snapshot of the given player.

        :param players.entity.Player player:
            The player for which a snapshot should be created.
        """
        return Snapshot(
            player.origin,
            player.angles,
            player.velocity,
            cls.create_bcmd_from_ucmd(player.playerinfo.last_user_command))

    @staticmethod
    def create_bcmd_from_ucmd(ucmd):
        """Transform a ``players.UserCmd`` into a ``players.bot.BotCmd``
        instance.

        :param UserCmd ucmd:
            The user command to transform.
        :rtype: BotCmd
        """
        bcmd = BotCmd()
        bcmd.reset()

        bcmd.command_number = ucmd.command_number
        bcmd.tick_count = ucmd.tick_count
        bcmd.view_angles = ucmd.view_angles
        bcmd.forward_move = ucmd.forward_move
        bcmd.side_move = ucmd.side_move
        bcmd.up_move = ucmd.up_move
        bcmd.buttons = ucmd.buttons
        bcmd.impulse = ucmd.impulse
        bcmd.weaponselect = ucmd.weaponselect
        bcmd.weaponsubtype = ucmd.weaponsubtype
        bcmd.random_seed = ucmd.random_seed
        bcmd.mousedx = ucmd.mousedx
        bcmd.mousedy = ucmd.mousedy
        bcmd.has_been_predicted = ucmd.has_been_predicted

        # TODO: Handle game specific attributes

        return bcmd


class PlayerState(IntEnum):
    """A class to store all possible player states."""

    PAUSED = 0
    PLAYING = 1
    STOPPED = 2


class Player(object):
    """A class to play a recording."""

    def __init__(self, recording, controller, replay_bot, adjust=True):
        """Initialize the player.

        :param Recording recording:
            The recording that should be player.
        :param BotController controller:
            The controller of the replay bot.
        :param players.entity.Player:
            The replay bot.
        :param bool adjust:
            If ``True`` the bot's location, angle and velocity is adjusted if
            it differs too much from the recorded state.
        """
        self.tick = 0
        self.recording = recording
        self.state = PlayerState.PAUSED
        self.controller = controller
        self.replay_bot = replay_bot
        self.adjust = adjust

    def __del__(self):
        """Remove all item from the replay bot and kick him."""
        self.controller.remove_all_items(True)
        self.replay_bot.kick()

    @property
    def replay_bot_name(self):
        """Return the name of the replay bot.

        :rtype: str
        """
        return f'{self.recording.player.name} (Replay Bot)'

    def start(self):
        """Start/restart the player (jump to the first snapshot."""
        self.tick = 0
        self.replay_bot.team = self.recording.player.team
        self.replay_bot.spawn(force=True)
        self.resume()

    def resume(self):
        """Resume the player."""
        self.state = PlayerState.PLAYING

    def pause(self):
        """Pause the player."""
        self.state = PlayerState.PAUSED

    def stop(self):
        """Stop the player (jump to the last snapshot)."""
        self.tick = len(self.recording) - 1
        self.state = PlayerState.STOPPED

    def remaining_time(self):
        """Return the remaining time of the recording in seconds.

        :rtype: float
        """
        return self.recording.duration - self.played_time

    def played_time(self):
        """Return the played time of the recording in seconds.

        :rtype: float
        """
        return self.tick * self.recording.tick_interval

    def handle_tick(self):
        if self.state != PlayerState.PLAYING:
            return

        snapshot = self.recording[self.tick]
        snapshot.replay_bcmd(self.controller)

        # Adjust location, angle and velocity if the recorded location differs
        # too much from the bot's location. This will allow a difference of 50
        # units.
        if self.tick == 0 or (self.adjust and self.replay_bot.origin.get_distance_sqr(snapshot.origin) > 2500):
            snapshot.replay_location(self.replay_bot)

        if self.tick < len(self.recording)-1:
            self.tick += 1
        else:
            self.state = PlayerState.STOPPED


class RecorderState(IntEnum):
    """A class to store all possible recorder states."""

    PAUSED = 0
    RECORDING = 1
    STOPPED = 2


class Recorder(object):
    """A class to record a player (client)."""

    def __init__(self, player):
        """Initialize the recorder.

        :param players.entity.Player player:
            The player to record.
        """
        self.player = player
        self.state = RecorderState.PAUSED
        self.recording = None

    def start(self):
        """Start/restart the recording. If a recording was already started, it
        is discarded.
        """
        self.recording = Recording(self.player)
        self.state = RecorderState.RECORDING

    def pause(self):
        """Pause the recorder."""
        if self.state == RecorderState.STOPPED:
            raise ValueError('Recorder is stopped.')

        self.state = RecorderState.PAUSED

    def resume(self):
        """Resume the recorder."""
        if self.state == RecorderState.STOPPED:
            raise ValueError('Recorder is stopped.')

        self.state = RecorderState.RECORDING

    def stop(self, save=True):
        """Stop the recorder. The current recording is added to the recording
        database.

        :param bool save:
            If ``True`` the recording is saved.
        :raise ValueError:
            Raised if the recorder was never started.
        """
        self.state = RecorderState.STOPPED

        if save and self.recording is not None and self.recording not in recording_mgr:
            recording_mgr.append(self.recording)

    def handle_tick(self):
        if self.state != RecorderState.RECORDING:
            return

        self.recording.add_snapshot(self.player)


class RecordingManager(list):
    """A class to manage recorders, players and recordings."""

    recorders = dict()
    players = dict()

    def remove_player(self, index):
        """Remove the player for the given replay bot index.

        :param int index:
            Index of the replay bot.
        """
        try:
            player = self.players[index]
        except KeyError:
            pass
        else:
            player.stop()
            del self.players[index]

    def get_player(self, recording, replay_bot_name=None):
        """Find a player for the given recording. If the recording is not being
        played, a new player is created.

        :param Recording recording:
            The recording to play.
        :param str replay_bot_name:
            Name of the replay bot if a new player is created.
        :rtype: Player
        """
        for player in self.players.values():
            if player.recording is recording:
                return player

        return self.create_player(recording, replay_bot_name)

    def create_player(self, recording, replay_bot_name=None, adjust=True):
        """Create a new player for the given recording.

        :param Recording recording:
            The recording to play.
        :param str replay_bot_name:
            The name that should be used for the replay bot. If ``None`` a name
            is generated.
        :param bool adjust:
            If ``True`` the bot's location, angle and velocity is adjusted if
            it differs too much from the recorded state.
        :raise ValueError:
            Raised if the recording is not playable, no replay bot could be
            created or the controller couldn't be retrieved.
        """
        if not recording.is_playable():
            raise ValueError('Recording is not playable.')

        if replay_bot_name is None:
            replay_bot_name = self.create_replay_bot_name(recording)

        replay_bot_edict = bot_manager.create_bot(replay_bot_name)
        if replay_bot_edict is None:
            raise ValueError('Failed to create a replay bot.')

        controller = bot_manager.get_bot_controller(replay_bot_edict)
        if controller is None:
            raise ValueError('Failed to get the bot controller.')

        replay_bot = SPPlayer(index_from_edict(replay_bot_edict))
        player = self.players[replay_bot.index] = Player(
            recording, controller, replay_bot, adjust)

        return player

    def create_replay_bot_name(self, recording):
        """Create a name for a replay bot.

        :param Recording recording:
            The recording that will be played.
        """
        return f'{recording.player.name} (Replay Bot)'

    def get_recorder(self, index):
        """Return the recorder for a given player/client.

        :param int index:
            The index of the player/client.
        :rtype: Recorder
        """
        try:
            return self.recorders[index]
        except KeyError:
            recorder = self.recorders[index] = Recorder(SPPlayer(index))
            return recorder

    def remove_recorder(self, index, save=True):
        """Remove the recorder for a given player/client.

        :param int index:
            The index of the player/client.
        :param bool save:
            If ``True`` the recording is saved.
        """
        try:
            recorder = self.recorders[index]
        except KeyError:
            return

        recorder.stop(save)
        del self.recorders[index]

recording_mgr = RecordingManager()


# ==============================================================================
# >> LISTENERS
# ==============================================================================
@OnTick
def on_tick():
    for recorder in recording_mgr.recorders.values():
        recorder.handle_tick()

    for player in recording_mgr.players.values():
        player.handle_tick()


@OnEntityDeleted
def on_entity_deleted(base_entity):
    if not base_entity.is_player():
        return

    index = base_entity.index
    recording_mgr.remove_recorder(index)
    recording_mgr.remove_player(index)


# ==============================================================================
# >> TEST
# ==============================================================================
from commands.typed import TypedSayCommand

from menus import PagedMenu
from menus import PagedOption

recording_menu = PagedMenu(title='Choose a recording to play:')

@recording_menu.register_build_callback
def on_menu_build(menu, index):
    menu.clear()

    for recording in recording_mgr:
        menu.append(PagedOption(
            '{} - {}'.format(
                time.strftime('%H:%M:%S', time.localtime(recording.creation_time)),
                round(recording.duration, 2)),
            recording))

@recording_menu.register_select_callback
def on_menu_select(menu, index, option):
    player = recording_mgr.create_player(option.value, adjust=False)
    player.start()
    return menu

@TypedSayCommand('!record')
def on_record(info):
    recorder = recording_mgr.get_recorder(info.index)
    recorder.start()

@TypedSayCommand('!stop')
def on_stop(info, x=None):
    recorder = recording_mgr.get_recorder(info.index)
    recorder.stop()

    print(recorder.recording.duration)

    if x is not None:
        recording_mgr.players.clear()

@TypedSayCommand('!pause')
def on_stop(info):
    for player in recording_mgr.players.values():
        player.pause()

@TypedSayCommand('!play')
def on_stop(info):
    recording_menu.send()