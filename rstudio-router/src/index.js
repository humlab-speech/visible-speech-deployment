const http = require('http');
const httpProxy = require('http-proxy');
const express = require('express');

const bodyParser = require('body-parser');
const child_process = require('child_process');
const Session = require('./Session.class.js');

const WebSocket = require('ws');

const webServerPort = 80;
const rstudioImageName = "hird-rstudio-emu";

const gitRepoAccessToken = process.env.GIT_API_ACCESS_TOKEN;
const hirdApiAccessToken = process.env.HIRD_API_ACCESS_TOKEN;

const sessions = [];

var app = express();
//let expressWs = require('express-ws')(app);
app.use(bodyParser.urlencoded({ extended: true }));


let wsServer = new WebSocket.Server({ noServer: true });
//const wsServer = new ws.Server({ noServer: false });
wsServer.on('connection', socket => {
  socket.on('message', message => console.log("ws msg:", message));
  
});

//var expressRouter = express.Router();

const proxyServer = httpProxy.createProxyServer({
  ws: true
});

function getSessionName(userId, projectId) {
  return "rstudio-session-p"+projectId+"u"+userId;
}

function closeOrphanContainers() {
  console.log("Closing orphan containers");
  let containers = getRunningContainers();
  containers.forEach((c) => {
    deleteContainer = true;
    sessions.forEach((s) => {
      if(c.id == s.shortDockerContainerId) {
        deleteContainer = false;
      }
    });

    if(deleteContainer) {
      let cmd = "docker stop "+c.id;
      child_process.exec(cmd, {}, () => {
        console.log("Stopped orphan container", c.id);
      });
    }
  });
}

function getAvailableSessionProxyPort() {
  let portMin = 30000;
  let portMax = 35000;
  let selectedPort = portMin;
  let selectedPortInUse = true;
  while(selectedPortInUse) {
    selectedPortInUse = false;
    for(let key in sessions) {
      if(sessions[key].port == selectedPort) {
        selectedPortInUse = true;
      }
    }
    if(selectedPortInUse) {
      if(selectedPort < portMax) {
        selectedPort++;
      }
      else {
        return false;
      }
    }
    else {
      return selectedPort;
    }
  }
  
  return false;
}

function stopContainer(containerId) {

}

function getRunningContainers() {
  let cmd = "docker ps --format='{{json .}}'";
  dockerContainersJson = child_process.execSync(cmd).toString('utf8');
  let containersJson = dockerContainersJson.split("\n");
  containersJson.pop();
  let sessions = [];
  containersJson.forEach((containerJson) => {
    let containerInfo = JSON.parse(containerJson);
    //Filter out non-rstudio
    if(containerInfo.Image == rstudioImageName) {
      sessions.push({
        id: containerInfo.ID,
        name: containerInfo.Names,
        runningFor: containerInfo.RunningFor,
        status: containerInfo.Status
      });
    }
  });
  return sessions;
}


function getCookies(req) {
  let cookiesParsed = [];
  let cookies = req.headers.cookie.split("; ");
  cookies.forEach((cookie) => {
    let cparts = cookie.split("=");
    let key = cparts[0];
    let value = cparts[1];
    cookiesParsed[key] = value;
  });

  return cookiesParsed;
}

function getSessionByCode(code) {
  let foundSession = false;
  sessions.forEach((session) => {
    if(session.accessCode == code) {
      foundSession = session;
    }
  });
  return foundSession;
}

function routeToRstudio(req, res = null, socket = null, ws = false, head = null) {
  let sessionAccessCode = null;

  /*
  if(socket != null) {
    console.log("socket", socket);
    socket = socket._socket;
  }
  */
  

  if(typeof req.headers.cookie != "undefined") {
    let cookies = req.headers.cookie.split("; ");
    cookies.forEach((cookie) => {
      let cparts = cookie.split("=");
      let key = cparts[0];
      let value = cparts[1];
      switch(key) {
        case "rstudioSession":
          sessionAccessCode = value;
          break;
      }
    });
  }

  if(sessionAccessCode == null) {
    console.log("Couldn't perform routing to rstudio because sessionAccessCode was null!");
    return false;
  }

  let sess = getSessionByCode(sessionAccessCode);
  if(sess === false) {
    console.log("Couldn't find a session with code", sessionAccessCode);
    console.log(sessions);
    //console.log("Tried to route to non-existing rstudio session!");
    return false;
  }

  //sess.proxySever.web(req, res);

  console.log("REQ:",req.url);

  //let proxyTargetAddress = 'http://'+sess.shortDockerContainerId+':'.sess.port;
  //let proxyTargetAddress = 'http://'+sess.shortDockerContainerId+':80';

  if(ws) {
    console.log("Performing websocket routing");
    //let proxyTargetAddress = 'ws://'+sess.shortDockerContainerId+':17890';
    //proxyServer.ws(req, res, { target: proxyTargetAddress });
    
    //sess.proxyServer.ws(req, socket, { target: "ws://"+sess.shortDockerContainerId+":17890" });
    sess.proxyServer.ws(req, socket, {
      //target: "ws://"+sess.shortDockerContainerId+":17890",
      target: "ws://localhost:17890",
      ws: true,
      xfwd: true
    });
  }
  else {
    console.log("Performing http routing");
    //let proxyTargetAddress = 'http://'+sess.shortDockerContainerId+':80';
    //proxyServer.web(req, res, { target: proxyTargetAddress });

    sess.proxyServer.web(req, res);
  }

  //proxyServer.web(req, res, { target: proxyTargetAddress, ws: true, xfwd: true, secure: false });
 
  //sess.proxyServer.ws(req, res, { target: 'ws://'+sess.shortDockerContainerId+':17890', ws: true });
  //sess.proxySocketServer.ws(req, res, { target: 'ws://'+sess.shortDockerContainerId+':17890', ws: true });
  //proxy.web(req, res, { target: proxyTargetAddress, ws: true });
}

function fetchActiveSessions() {
  let containers = getRunningSessions();
  return containers;
}

function getSession(userId, projectId) {
  //
}

//
// Create your custom server and just call `proxy.web()` to proxy
// a web request to the target passed in the options
// also you can use `proxy.ws()` to proxy a websockets request
//


function checkAccessCode(req) {
  if(req.headers.hird_api_access_token != hirdApiAccessToken || typeof hirdApiAccessToken == "undefined") {
    console.log("Error: Invalid hird_api_access_token! Ignoring request.");
    return false;
  }
  return true;
}

app.get('/*', (req, res, next) => {
  let parts = req.url.split("/");
  console.log(req.url);
  if(parts[1] != "api") {
    //console.log("abc", parts);
    routeToRstudio(req, res);
  }
  else {
    next();
  }
});

app.post('/*', (req, res, next) => {
  let parts = req.url.split("/");
  if(parts[1] != "api") {
    routeToRstudio(req, res);
  }
  else {
    next();
  }
});

app.get('/api/*', (req, res, next) => {
  /*
  if(!checkAccessCode(req)) {
    res.end("{ 'status': 'bad access code' }");
  }
  */
  next();
});
/*
app.get('/api/:id', (req, res) => {
  console.log("new", req.url);
});
*/

function removeSession(session) {
  for(let i = sessions.length-1; i > -1; i--) {
    if(sessions[i].accessCode == session.accessCode) {
      sessions.splice(i, 1);
    }
  }
}

function getUserSessions(userId) {
  console.log("Getting user sessions for user", userId);
  let userSessions = [];
  for(let key in sessions) {
    if(sessions[key].user.id == userId) {
      userSessions.push({
        sessionCode: sessions[key].accessCode,
        projectId: sessions[key].project.id,
        'type': 'rstudio'
      });
    }
  }
  return userSessions;
}

app.get('/api/sessions/:user_id', (req, res) => {
  let out = JSON.stringify(getUserSessions(req.params.user_id));
  res.end(out);
});

app.get('/api/session/:session_id/commit', (req, res) => {
  let sess = getSessionByCode(req.params.session_id);
  if(sess === false) {
    //Todo: Add error handling here if session doesn't exist
    res.end(`{ "msg": "Session does not exist", "level": "error" }`);
  }
  sess.commit().then((result) => {
    console.log(result);
    res.end(`{ "msg": "Committed ${result}", "level": "info" }`);
  }).catch((e) => {
    console.error("Error:"+e.toString('utf8'));
  });
});

app.get('/api/session/:session_id/delete', (req, res) => {
  let sess = getSessionByCode(req.params.session_id);
  if(sess === false) {
    console.error("Error on delete: Session not found!");
    res.end(`{ "msg": "Error on delete: Session not found! Session id:${req.params.session_id}", "level": "error" }`);
    return false;
  }
  sess.delete().then(() => {
    let sessId = sess.accessCode;
    removeSession(sess);
    res.end(`{ "deleted": "${sessId}" }`);
  }).catch((e) => {
    console.log(e.toString('utf8'));
  });
});

//This asks to create a new session for this user/project
app.post('/api/session/user', (req, res) => {
  let user = JSON.parse(req.body.gitlabUser);
  let project = JSON.parse(req.body.gitlabProject);
  console.log("Received request access session for user", user.id, "and project", project.id, "with session", req.body.rstudioSession);
  
  if(typeof req.body.rstudioSession != "undefined") {
    let sess = getSessionByCode(req.body.rstudioSession);
    if(sess !== false) {
      //Since session was found, return the access code to it - which tells the client to use this to connect to the existing session instead
      console.log("Existing session was found, sending project access code to api/proxy ")
      res.end(JSON.stringify({
        sessionAccessCode: sess.accessCode
      }));
  
      return;
    }
  }
  

  console.log("No existing session was found, creating container");
  (async () => {
    let sess = new Session(user, project, getAvailableSessionProxyPort());

    //console.log(sess);

    let containerId = await sess.createContainer();
    let gitOutput = await sess.cloneProjectFromGit();
    sessions.push(sess);
    return sess;
  })().then((sess) => {
    console.log("Creating container complete, sending project access code to api/proxy");
    res.end(JSON.stringify({
      sessionAccessCode: sess.accessCode
    }));
  });

});

app.get('/api/session/commit/user/:user_id/project/:project_id/projectpath/:project_path', (req, res) => {
  console.log("Received request to commit session for user", req.params.user_id, "and project", req.params.project_id);
  
  /*
  let cookies = getCookies(req);
  let sess = getSessionByCode(cookies['rstudioSession']);
  */

});

closeOrphanContainers();
/*
expressWs.getWss().on('connection', function(ws) {
  console.log('ws connection open');
});
*/
/*
app.ws('/*', function (ws, req) {

  console.log("New ws connection has opened!");

  ws.on('close', function() {
      console.log('The ws connection was closed!');
  });

  ws.on('message', function (message) {
      console.log('Message (ws) received: '+message);
  });
});

app.on('upgrade', function (req, socket, head) {
  console.log("Router received http 'upgrade' request");
  routeToRstudio(req, socket, head);
  //proxy.ws(req, socket, head);
});
*/

const server = app.listen(webServerPort, () => {
  console.log("Listening on port", webServerPort);
});

server.on('upgrade', (request, socket, head) => {
  console.log("upgrade!");
  wsServer.handleUpgrade(request, socket, head, (webSocket) => {
    wsServer.emit('connection', webSocket, request);
    routeToRstudio(request, null, socket, true, head);
  });
});
