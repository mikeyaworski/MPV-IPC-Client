# MPV IPC Client

This is a (Python) client for interacting with an MPV instance via IPC.
The MPV instance is responsible (likely via a client script) for constructing the IPC server path (named pipe on Windows, Unix socket on Linxu/macOS).

## Installation / Local Development

1. Reference this remote GitHub repo directly:
    ```
    git+ssh://git@github.com/mikeyaworski/MPV-IPC-Client.git@master#egg=mpv_ipc_client
    ```
    You can `pip install` that URI or add it to your `requirements.txt` file, etc.
1. Clone this repo and install this package locally (for local development):
    ```
    -e /full/path/to/MPV-IPC-Client
    ```

## Usage

### Python client

```python
from mpv_ipc_client import MpvIpcClient

mpv_ipc_client = MpvIpcClient(arg_name='--mpv-ipc-server', should_panic=True)
mpv_ipc_client.pause()
mpv_ipc_client.close_connection()
```

### MPV instance client script

The MPV instance (likely via a client script) is responsible for constructing the IPC server path and setting the property `input-ipc-server` on the MPV instance. This sets the MPV instance up as the receiving end of the IPC commands.

There are numerous ways to launch terminals and subprocesses to run your Python script from an MPV client script. I'll provide a JavaScript example for Windows. Remember that the JS interpreter for MPV is MuJS, which is a minimal ES5 interpreter. You may launch scripts in an alternative way, on an alternative OS, or with Lua.

```javascript
var is_windows = !!mp.utils.getenv('OS');

var create_unique_ipc_server = (function() {
  return function() {
    var current_socket_property = mp.get_property('input-ipc-server');
    if (current_socket_property) {
      return current_socket_property;
    }

    // Generate a unique socket name using timestamp or random number
    var timestamp = Date.now();
    var socket_name;
    
    if (is_windows) { // Windows
      socket_name = '\\\\.\\pipe\\mpv-socket-' + timestamp;
    } else { // Linux/macOS
      socket_name = '/tmp/mpv-socket-' + timestamp;
    }
    
    // Enable IPC server dynamically
    mp.set_property('input-ipc-server', socket_name);
    mp.osd_message('IPC server created: ' + socket_name, 3);
    return socket_name;
  }
})();

var script_path = '/full/path/to/some/script.py';
var socket = create_unique_ipc_server();
mp.commandv.apply(null, [
  'run',
  'cmd',
  '/c',
  'start',
  'cmd',
  '/c',
  'python',
  script_path,
  '--mpv-ipc-server',
  socket,
]);
```
