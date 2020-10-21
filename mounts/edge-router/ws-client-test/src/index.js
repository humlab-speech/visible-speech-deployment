const WebSocket = require('ws');

const ws = new WebSocket('ws://websocket-server:17890/path');

ws.on('open', function open() {
  ws.send('something from client');
});

ws.on('message', function incoming(data) {
  console.log(data);
});
