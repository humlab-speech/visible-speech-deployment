
class Dashboard {
    constructor(app) {
        this.app = app;

        
        /*
        $("#magic-btn").on("click", (evt) => {
            console.log("magick!");
            console.log(this.app.user.projects);
        });
        */


    }

    render() {
        console.log("dashboard->render");
        let dashNode = $("#dashboard-tpl")[0].cloneNode(true);
        $(dashNode).attr("id", "dashboard-container");

        if(this.app.user.gitlabUser != null) {
            $(dashNode).find(".user_gravatar").attr("src", this.app.user.gitlabUser.avatar_url);
        }

        $(dashNode).find(".user_displayname").text(this.app.user.displayName);
        $(dashNode).find(".user_email").text(this.app.user.email);

        $("#content-container").append(dashNode);


        $(".dashboard-projects-create-btn").on("click", () => {
            console.log("Create project button clicked!");
            //JSSPARouter.gotoPath('/dashboard/create-project');
            $("#project-create-form").show();
        });

        $("#project-create-submit").on("click", () => {
            this.createProject();
        });

        $("#sign-out-btn").on("click", (evt) => {
            console.log("Sign-out");
            console.log(document.cookie);
            document.cookie = "PHPSESSID=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
        });
        
        this.fetchProjects().then((projects) => {
            console.log(projects);
            this.app.projects = projects;
            this.renderProjects(projects);
        });
    }

    createProject() {
        console.log("Creating project");
        return new Promise((resolve, reject) => {
            $.ajax("/api/v1/user/project", {
                dataType: "json",
                method: "post",
                data: {
                    name: $(".dashboard-projects-create-name").val()
                },
                success: (data) => {
                    console.log(data);
                    this.app.dashboard.fetchProjects().then((projects) => {
                        console.log("fetch projects", projects)
                        this.app.dashboard.renderProjects(projects);
                    });
                    //JSSPARouter.gotoPath('/dashboard');
                }
            });
        });
    }

    getCookies() {
        let cookiesParsed = [];
        let cookies = document.cookie.split("; ");
        cookies.forEach((cookie) => {
            let cparts = cookie.split("=");
            let key = cparts[0];
            let value = cparts[1];
            cookiesParsed[key] = value;
        });
    
        return cookiesParsed;
    }

    statusMsg(projectId, msg = "&nbsp;", fadeOutTime = false) {
        console.log(projectId, msg);
        let content = "";
        if(msg != "&nbsp;") {
            content = "<img class=\"project-loading-indicator\" src=\"/assets/loading.gif\" /><span>"+msg+"</span>";
        }
        else {
            content = "<span>"+msg+"</span>";
        }
        
        
        $("[project-id="+projectId+"] .project-progress-info-box").html(content);

        if(fadeOutTime !== false) {
            console.log("Fade out");
            $("[project-id="+projectId+"] .project-progress-info-box").fadeOut(fadeOutTime);
        }

    }


    async fetchProjects() {
        let returnData = null;
        await new Promise((resolve, reject) => {
            $.ajax("/api/v1/user/project", {
                dataType: "json",
                success: (projects) => {
                    console.log(projects);
                    resolve(projects);
                    //app.dashboard.renderProjects(projects);
                }
            });
        }).then((projects) => {
            returnData = projects;
        });
        return returnData;
    }

    renderProjects(projects = null) {

        if(projects == null) {
            projects = this.app.projects;
        }

        $("#main-container .projects_list").html("");
        projects.forEach((project) => {
            let projectBox = "<div project-id='"+project.id+"'>"+project.name+"<br />";
            projectBox += "<a href='"+project.web_url+"'>View project in GitLab</a><br />";
            projectBox += "<button class='rstudio-launch-btn'>Launch in RStudio</button>";
            if(project.sessions.length > 0) {
                console.log(project.sessions);
                projectBox += "<button class='rstudio-commit-btn'>Commit & push changes</button>";
                projectBox += "<button class='rstudio-close-btn'>Shutdown session</button><br />";
            }
            projectBox += "<button class='emu-webapp-launch-btn'>Launch in EMU webApp</button>";

            projectBox += "<div class='project-progress-info-box'>&nbsp;</div>";
            
            projectBox += "</div>";

            $("#main-container .projects_list").append("<li>"+projectBox+"</li>");

            this.statusMsg(project.id);
        });

        
        $(".emu-webapp-launch-btn").on("click", (evt) => {
            let projectId = $(evt.currentTarget.parentElement).attr("project-id");
            
            let project = null;
            this.app.projects.forEach((p) => {
                if(p.id == projectId) {
                    project = p;
                }
            });

            

        });

        $(".rstudio-launch-btn").on("click", (evt) => {
            console.log();

            //This should:
            //1. Contact HIRD-API about creating a new container in the name of this user and with this project loaded
            //2. HIRD-API tells rstudio-router to make the container
            //3. rstudio-router responds with an access code specific for this session/container
            //4. access code is passed back to web client (here)
            //5. access code is set as cookie
            //6. Redirect is done to https://rstudio.localtest.me

            let projectId = $(evt.currentTarget.parentElement).attr("project-id");
            
            let project = null;
            this.app.projects.forEach((p) => {
                if(p.id == projectId) {
                    project = p;
                }
            });


            //If there already seems to be a running session here and we have an access code for it, set the cookie to this so that we get routed into this container
            if(project.sessions.length > 0) {
                this.statusMsg(projectId, "Creating your environment...");
                document.cookie = "rstudioSession="+project.sessions[0].sessionCode+"; domain="+this.app.domain;
            }
            else {
                this.statusMsg(projectId, "Loading environment...");
                document.cookie = "rstudioSession=''; domain="+this.app.domain;
            }

            $.ajax("/api/v1/rstudio/session/please", {
                method: "post",
                data: {
                    projectId: projectId
                },
                success: (data) => {
                    console.log(data);
                    this.statusMsg(projectId, "Taking you there...");
                    let sessionCode = JSON.parse(data).sessionAccessCode;
                    /*
                    for(let key in this.app.user.projects) {
                        if(this.app.user.projects[key].id == projectId) {
                            this.app.user.projects[key].hird = {
                                rstudioSessionCode: sessionCode
                            };
                        }
                    }
                    */

                    //Server will always respond with sessionCode, which is a new code in case a new container was spawned, and the same code we already had otherwise, in which case it doesn hurt to overwrite it 
                    document.cookie = "rstudioSession="+sessionCode+"; domain="+this.app.domain;
                    window.location.href = "https://rstudio."+this.app.domain;
                }
            });

            /*
            let rstudioSessionUrl = "https://rstudio.localtest.me";

            let project = null;
            this.app.user.projects.forEach((p) => {
                if(p.id == projectId) {
                    project = p;
                }
            });

            document.cookie = "userId="+this.app.user.gitlabUser.id+"; domain=localtest.me";
            document.cookie = "projectId="+projectId+"; domain=localtest.me";
            document.cookie = "gitlabUserName="+this.app.user.gitlabUser.username+"; domain=localtest.me";
            document.cookie = "gitlabProjectPath="+project.http_url_to_repo+"; domain=localtest.me";
            window.location.href = rstudioSessionUrl;
            */
        });

        $(".rstudio-commit-btn").on("click", async (evt) => { 
            let projectId = $(evt.currentTarget.parentElement).attr("project-id");
            this.statusMsg(projectId, "Committing & pushing...");
            let project = null;
            this.app.projects.forEach((p) => {
                if(p.id == projectId) {
                    project = p;
                }
            });

            //let c = document.cookie;
            let cookies = this.getCookies();
            console.log("Save session", cookies['rstudioSession']);

            // not handling potential merge conflicts here
            new Promise((resolve, reject) => {
                $.ajax("/api/v1/rstudio/save", {
                    method: "post",
                    data: {
                        rstudioSession: cookies['rstudioSession']
                    },
                    success: (data) => {
                        console.log(data);
                        this.statusMsg(projectId);
                        resolve(data);
                    }
                });
            });
        });

        $(".rstudio-close-btn").on("click", async (evt) => {
            let projectId = $(evt.currentTarget.parentElement).attr("project-id");
            this.statusMsg(projectId, "Deleting environment...");
            let project = null;
            this.app.projects.forEach((p) => {
                if(p.id == projectId) {
                    project = p;
                }
            });

            console.log("Shutdown container", project.sessions[0].sessionCode);

            await new Promise((resolve, reject) => {
                $.ajax("/api/v1/rstudio/close", {
                    method: "post",
                    data: {
                        rstudioSession: project.sessions[0].sessionCode
                    },
                    success: (data) => {
                        let deletedInfo = JSON.parse(data);
                        console.log(deletedInfo);
                        this.statusMsg(projectId);
                        this.app.projects.forEach((project) => {
                            project.sessions.forEach((session) => {
                                if(session.sessionCode == deletedInfo.deleted) {
                                    project.sessions = [];
                                }
                            });
                        });

                        //FIXME: THis re-renders projects, but the projects has not actually been updated to show that this container is no longer running
                        this.app.dashboard.renderProjects();
                        //JSSPARouter.gotoPath('/dashboard');
                        resolve(data);
                    },
                    error: (e) => {
                        console.log("delete callback error", e);
                        this.statusMsg(projectId, e, 5000);
                    },
                    complete: () => {
                        console.log("delete callback complete");
                    }
                });
            });
            
            
        });
    }

}

export { Dashboard as default };