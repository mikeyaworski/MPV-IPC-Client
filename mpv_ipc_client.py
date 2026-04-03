import json
import argparse
import os
import sys
from typing import Final, TYPE_CHECKING

JsonValue = str | int | float | bool
Command = list[JsonValue]

IS_WINDOWS: Final = os.name == 'nt'

if IS_WINDOWS:
  # Windows named pipe
  import win32file
else:
  # Unix socket (Linux/macOS)
  import socket

# Always visible to type checker since it doesn't work well with the IS_WINDOWS condition.
if TYPE_CHECKING:
  import win32file  
  import socket

class MpvIpcClient:
  '''
  Singleton class that manages a connection to the MPV IPC server (server path provided via CLI argument).
  Establish the connection, send commands, read responses and close the connection.

  Usage:
      ```python
      client = MpvIpcClient(arg_name='--ipc-server', should_panic=True)
      client.set_property('title', ['Hello World'])
      client.close_connection()
      ```

  Args:
      arg_name (str): Name of CLI argument to read the IPC server location. Defaults to `--ipc-server`
      should_panic (bool): Whether to exit the program if the connection cannot be established.
  '''

  _instance = None

  # Read one character at a time until we see a newline character.
  # This is the only way we know when a message is complete.
  # Reading more than one character runs the risk of reading into the next message.
  _RECV_BUFFER_SIZE = 1

  def __new__(cls, **kwargs):
    if cls._instance is None: 
      cls._instance = super().__new__(cls)
      cls._instance._initialized = False
    return cls._instance

  def __init__(self, **kwargs):
    if self._initialized: return
    self._conn_, self._ipc_server_path = self._get_ipc_connection_from_args(**kwargs)
    self._request_id_counter = 0
    self._initialized = True

  @property
  def connected(self):
    return self._conn_ is not None
  
  @property
  def ipc_server_path(self):
    return self._ipc_server_path

  @property
  def _conn(self):
    '''
    Throws a RuntimeError if there is no connection to the MPV IPC server.
    '''
    if not self.connected:
      raise RuntimeError('No connection to MPV IPC server')
    return self._conn_

  def __repr__(self):
    return f"<MpvIpcClient singleton, connected={self.connected}>"

  def _get_ipc_connection(self, ipc_server_path: str):
    '''
    Establishes a connection to the MPV IPC server at the given path.
    The path should be created by the MPV client (e.g. by a client script) and passed to this script.
    The path will be a named pipe on Windows, or a Unix socket on Linux/macOS.

    Args:
        ipc_server_path (str): The path to the MPV IPC server (e.g. from the `--input-ipc-server` argument).
    Returns:
        A Windows file handle on Windows, or a socket object on Linux/macOS.
    '''
    try:
      if IS_WINDOWS:
        # Windows named pipe
        handle = win32file.CreateFile(
          ipc_server_path,
          win32file.GENERIC_READ | win32file.GENERIC_WRITE,
          0, None,
          win32file.OPEN_EXISTING,
          0, None
        )
        return handle
      else:
        # Unix socket
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(ipc_server_path)
        return sock
    except Exception as e:
      print(f'Error communicating with mpv: {e}', file=sys.stderr)
      return None

  def _get_ipc_connection_from_args(self, arg_name: str = '--ipc-server', *, should_panic=False):
    '''
    Parses the CLI arguments to find the location of the IPC server.

    Args:
        Find details in the documentation for the MpvIpcClient constructor.
    Returns:
        The connection to the IPC server (Windows file handle or Unix socket), and the IPC server path.
    '''
    parser = argparse.ArgumentParser()
    parser.add_argument(arg_name, type=str, help='Socket to make commands to MPV instance')
    args, unknown = parser.parse_known_args()
    ipc_server_arg = getattr(args, arg_name.lstrip('-').replace('-', '_'), None)
    if ipc_server_arg:
      return self._get_ipc_connection(ipc_server_arg), ipc_server_arg
    if should_panic:
      print('Failed to connect to MPV', file=sys.stderr)
      input('Press Enter to exit...')
      exit(1)
    else:
      return None, ipc_server_arg

  def close_connection(self):
    '''
    Closes the connection to the MPV IPC server.
    Deletes the --input-ipc-server property on the MPV instance.
    MPV will close the connection on its end as a result.
    '''
    self.set_property('input-ipc-server', [''])
    try:
      if IS_WINDOWS:
        win32file.CloseHandle(self._conn)
      else:
        # On Unix, don't explicitly close the socket; MPV will close it when disabling IPC.
        # conn.close()
        pass
      self._conn_ = None
    except Exception as e:
      print(f'Error closing connection: {e}', file=sys.stderr)

  def _get_next_request_id(self):
    self._request_id_counter += 1
    return self._request_id_counter

  def read_response(self) -> str | None:
    '''
    Reads response from MPV IPC server (character-by-character) until a newline character appears.
    Documentation for this newline character: https://mpv.io/manual/master/#protocol.
    "All commands, replies, and events are separated from each other with a line break character."

    Returns:
      str | None: Returns the serialized JSON response (as a string).
        Returns `None` if there was an error reading from the connection.
    '''
    buffer = bytearray()
    try:
      while True:
        if IS_WINDOWS:
          result, data = win32file.ReadFile(self._conn, self._RECV_BUFFER_SIZE)
        else:
          data: bytes = self._conn.recv(self._RECV_BUFFER_SIZE)
        if not data: break
        buffer.extend(data)
        if data == b'\n': break

      if buffer: return buffer.decode()
    except Exception as e:
      print(f'Error reading response from mpv: {e}', file=sys.stderr)
    return None

  def read_response_for_request_id(self, request_id, max_messages=None):
    '''
    Read and parse lines until we find the response with a matching request_id.
    Args:
        request_id (int): The request_id to match in the responses
        max_messages (int | None): Maximum number of messages to read before giving up
    Returns:
        dict: The parsed JSON response.
          Information about the response format is here: https://mpv.io/manual/master/#protocol.
          The dict will contain an `request_id` to indentify it.
    '''
    messages_read = 0
    while True:
      if max_messages and messages_read >= max_messages:
        return None
      response = self.read_response()
      if not response:
        return None
      messages_read += 1
      try:
        response_json = json.loads(response.strip())
        if response_json.get('request_id') == request_id:
          return response_json
      except json.JSONDecodeError:
        print(f'Invalid JSON response: {response}', file=sys.stderr)

  @staticmethod
  def _get_command_bytes(command: Command, request_id: int):
    '''
    Constructs a command message to be sent to MPV over IPC.
    Args:
        command (list[str | int | float | bool]): The command to send.
        request_id (int): The request_id to include in the command message.
          This will be used to find a matching response.
    Returns:
        bytes: The command serialized as JSON and encoded as bytes.
    '''
    command_json = json.dumps({
      'command': command,
      'request_id': request_id
    }) + '\n'
    return command_json.encode()

  def send_command(self, command: Command) -> dict | None:
    '''
    Send a command to MPV over the IPC connection.
    Captures the response, which is necessary to prevent the pipe from filling up without emptying.
    Args:
        command (list[str | int | float | bool]): The command to send.
          The first element being the name of the command,
          and the following elements being parameters to the command, which are native JSON values.
    Returns:
        dict: The parsed JSON response.
          Information about the response format is here: https://mpv.io/manual/master/#protocol.
          The dict will contain an `error` key indicating success/failure.
          It will contain a `data` key with a value if relevant for the command.
    '''
    request_id = self._get_next_request_id()
    try:
      command_bytes = self._get_command_bytes(command, request_id)
      if IS_WINDOWS: win32file.WriteFile(self._conn, command_bytes)
      else: self._conn.send(command_bytes)
      return self.read_response_for_request_id(request_id)
    except Exception as e:
      print(f'Error sending command to mpv: {e}', file=sys.stderr)
    return None

  def send_commands(self, commands: list[Command], **kwargs):
    for command in commands:
      self.send_command(command, **kwargs)

  def set_property(self, name: str, args: JsonValue | list[JsonValue] = []):
    '''
    Sends a command to MPV to set a property.
    Args:
        name (str): The name of the property to set (e.g. 'pause', 'playlist-pos', etc.)
        args (JsonValue | list[JsonValue]): The value(s) to set the property to. This will be unpacked into the command.
    '''
    if not isinstance(args, list): args = [args]
    command = ['set_property', name] + args
    self.send_command(command)

  def get_property(self, property_name: str):
    '''
    Get the value of a specific property from the MPV instance.
    Sends a command to get the property and processes the response.
    Handles errors by printing them to stderr.

    Args:
        property_name (str): The name of the property to get (e.g. 'pause', 'playlist-pos', etc.)
    Returns:
        The value of the property (`data` field of the response) if successful, or `None` if there was an error.
    '''
    response = self.send_command(['get_property', property_name])
    if not response:
      print(f'No response for get_property {property_name}', file=sys.stderr)
      return None
    error_status = response.get('error')
    if error_status == 'success':
      return response.get('data')
    print(f'Error getting {property_name}: {error_status}', file=sys.stderr)
    return None

  ###################################################################
  ### Above this line are methods core to the client API.         ###
  ### Below this line are useful helper methods and abstractions. ###
  ###################################################################

  def show_text(self, text: str, duration_ms: int = 3000):
    self.send_command(['show-text', text, duration_ms])

  def clear_playlist(self):
    self.send_commands([
      ['playlist-clear'],
      ['playlist-remove', 'current'],
    ])

  def move_playlist_item(self, from_index: int, to_index: int):
    self.send_command(['playlist-move', from_index, to_index])

  def remove_playlist_item(self, index: int):
    self.send_command(['playlist-remove', index])

  def save_file_position(self):
    self.send_command(['write-watch-later-config'])

  def reload_file(self):
    self.save_file_position()
    self.send_command(['playlist-play-index', 'current'])
    self.show_text('File reloaded')

  def pause(self):
    self.set_property('pause', [True])

  def unpause(self):
    self.set_property('pause', [False])

  def safe_remove_current(self):
    '''
    Safely removes current item from playlist by first ensuring we are not at the end of the playlist.
    If at the end of the playlist, first move back in the playlist and then remove the item.
    This is needed, because if one removes the current playlist item with nothing else after it in the playlist,
    the default behavior of MPV is to break and the playback.
    If the playlist is already empty, quits MPV.
    '''
    count = self.get_property('playlist-count') or 0
    if count == 0:
      self.send_command(['quit'])
      return
    self.save_file_position()
    index = self.get_property('playlist-pos') or 0
    if index == count - 1:
      # We're at the last item. Move to previous first
      self.send_command(['set_property', 'playlist-pos', index - 1])
      self.send_command(['playlist-remove', index])
    else:
      # Not the last item, just remove it
      self.send_command(['playlist-remove', index])

  def quit_if_empty(self):
    count = self.get_property('playlist-count') or 0
    if count == 0: self.send_command(['quit'])

  def safe_remove_current_and_quit(self):
    self.safe_remove_current()
    self.quit_if_empty()

  def replace_current_file_in_playlist(self, uri):
    current_pos: int | None = self.get_property('playlist-pos')
    if current_pos is None: return
    self.send_commands([
      ['loadfile', uri, 'insert-next'],
      ['playlist-remove', current_pos],
    ])

  def get_file_paths(self, is_playlist: bool):
    '''
    Gets the file paths of the current playlist or current file.
    Args:
        is_playlist (bool): Whether to get the file paths of the entire playlist (True) or just the current file (False).
    Returns:
        list[str] | None: A list of file paths if successful, otherwise `None`.
    '''
    playlist = self.get_property('playlist')
    if not playlist: return None
    current_file = self.get_property('path')
    if not current_file: return None
    file_paths = (
      [item.get('filename') for item in playlist if item.get('filename')]
      if is_playlist
      else [current_file]
    )
    return [path.strip() for path in file_paths]
