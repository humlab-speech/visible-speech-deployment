const nanoid = require('nanoid');
const child_process = require('child_process');
const httpProxy = require('http-proxy');

class Session {
    constructor(user, project, port) {
        this.user = user;
        this.project = project;
        this.port = port;
        this.port = 8787;
        this.accessCode = nanoid.nanoid(32);
        this.fullDockerContainerId = null;
        this.shortDockerContainerId = null;
        this.rstudioImageName = "hird-rstudio-emu";
    }

    getContainerName(userId, projectId) {
        let salt = nanoid.nanoid(4);
        return "rstudio-session-p"+projectId+"u"+userId+"-"+salt;
    }

    async createContainer() {
        console.log("Creating new project container");

        let sessionWsPort = 17890;
        // -p "+sessionWsPort+":17890
        let cmd = "docker run --rm -e DISABLE_AUTH=true --network humlab-speech-deployment_hird-net --name "+this.getContainerName(this.user.id, this.project.id)+" -d "+this.rstudioImageName;
            
        let dockerContainerId = null;
        try {
            dockerContainerId = child_process.execSync(cmd);
        }
        catch(e) {
            console.log("Error:", e);
        }
        this.fullDockerContainerId = dockerContainerId.toString('utf8');
        this.shortDockerContainerId = this.fullDockerContainerId.substring(0, 12);

        //Setting up proxy server
        this.proxyServer = httpProxy.createProxyServer({
            target: "http://"+this.shortDockerContainerId+':8787',
            //port: 8787,
            ws: true
        });

        this.proxyServer.on('error', function (err, req, res) {
            console.log("Proxy error!");
            console.log(err);
        });

        this.proxyServer.on('proxyReq', (err, req, res) => {
            //console.log("Rstudio-router session proxy received request!");
        });

        this.proxyServer.on('proxyReqWs', (err, req, res) => {
            console.log("Rstudio-router session proxy received ws request!");
            //Can we redirect this request to the 17890 port here?
        });

        this.proxyServer.on('upgrade', function (req, socket, head) {
            console.log("Rstudio-router session proxy received upgrade!");
            //this.proxyServer.proxy.ws(req, socket, head);
        });


        //FIXME: Need a better way to check if container is ready than to just sleep for a while
        await new Promise((resolve, reject) => {
            setTimeout(() => {
                console.log("New project container created");
                resolve();
            }, 3000);
        });
        
        return this.shortDockerContainerId;
    }

    async cloneProjectFromGit() {
        //2. git clone project into container
        console.log("Cloning project into container");
        let crendentials = "root:"+process.env.GIT_API_ACCESS_TOKEN;
        let gitRepoUrl = "http://"+crendentials+"@gitlab:80/"+this.project.path_with_namespace+".git";
        let targetPath = "/home/rstudio/project";
        let cmd = "docker exec "+this.shortDockerContainerId+" git clone "+gitRepoUrl+" "+targetPath;
        let output = child_process.execSync(cmd);

        cmd = "docker exec "+this.shortDockerContainerId+" chown -R rstudio:rstudio "+targetPath;
        child_process.execSync(cmd);

        /*
        cmd = "docker exec "+this.shortDockerContainerId+" echo 'library(emuR)' | /usr/local/bin/R";
        child_process.execSync(cmd);
        */

        console.log("Project cloned into container");
        output = output.toString('utf8');
        return output;
    }

    async commit() {
        console.log("Committing project");
        //let crendentials = "root:"+process.env.GIT_API_ACCESS_TOKEN;
        //let gitRepoUrl = "http://"+crendentials+"@gitlab:80/"+this.projectPath+".git";

        
        let cmd = "";
        let output = "";
        cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" git config --global user.email '"+this.user.email+"'";
        console.log(cmd);
        output = child_process.execSync(cmd);
        console.log(output.toString('utf8'));

        cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" git config --global user.name '"+this.user.name+"'";
        console.log(cmd);
        output = child_process.execSync(cmd);
        console.log(output.toString('utf8'));
        //docker exec 39d8b5d4dd4b bash -c "cd /home/rstudio/project && git commit -m 'hird-auto-commit' && git push"

        cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" bash -c 'git add . && git commit -m \"system-auto-commit\" && git push'";
        console.log(cmd);
        output = child_process.exec(cmd, {}, (error, stdout, stderr) => {
            if (error) {
                console.error(`exec error: ${error}`);
                //return;
              }
              console.log(`output: ${output}`);
              console.log(`stdout: ${stdout}`);
              console.error(`stderr: ${stderr}`);

              //return output.toString('utf8');
        });
        return this.accessCode;

        /*
        cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" git add .";
        console.log(cmd);
        output = child_process.execSync(cmd);
        console.log(output.toString('utf8'));

        //cmd = "bash -c docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" git commit -m 'hird-auto-commit' && git push";
        //cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" git commit -m 'hird-auto-commit'";
        //cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" bash -c 'git commit -m hird-auto-commit'";
        let params = [
            "exec",
            this.shortDockerContainerId,
            "git commit -m 'hird-auto-commit'"
        ];

        output = child_process.spawnSync("docker", params, {
            cwd: '/home/rstudio/project',
            shell: true
        });

        console.log("A");d
        console.log(output.output);
        console.log("B");

        cmd = "docker exec -w /home/rstudio/project "+this.shortDockerContainerId+" git push";
        console.log(cmd);
        output = child_process.execSync(cmd);
        console.log(output.toString('utf8'));
        

        return output.toString('utf8');
        */
    }

    async delete() {
        console.log("Deleting session");
        
        //This will stop new connections but not close existing ones
        this.proxyServer.close();

        setTimeout(() => {
            let cmd = "docker stop "+this.shortDockerContainerId;
            console.log(cmd);
            let output = child_process.execSync(cmd);
            console.log(output.toString('utf8'));
        }, 5000);

        
        return this.accessCode;
    }
};

module.exports = Session
