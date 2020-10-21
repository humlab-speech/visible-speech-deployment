import Dashboard from "./Dashboard.class";
import User from "./User.class";

class Application {
    constructor() {
        this.domain = window.location.hostname;
        this.dashboard = new Dashboard(this);
        this.user = null;
        this.projects = null;

        /*
        this.jssparouter = JSSPARouter.map({
            "/": {
              id: 'root',
              path: '/',
              callback: this.callback,
              index: '/',
              child: {
                "auth": {
                    id: 'auth',
                    path: 'auth',
                    callback: () => {
                        console.log("Auth callback!");
                      //this.user.render();
                    }
                },
                "dashboard": {
                  id: 'dashboard',
                  path: 'dashboard',
                  callback: () => {
                    this.dashboard.render();
                  },
                  child: {
                    "settings": {
                        id: 'settings',
                        path: 'settings',
                        callback: this.callback
                        },
                    "create-project": {
                        id: 'create-project',
                        path: 'create-project',
                        callback: this.createProject
                        }
                    }
                }
            }
        }});
        */
        
    
        
        $("#signin-btn").on("click", () => {
            console.log("Sign-in requested");

            $.ajax("/api/v1/session", {
                success: (data) => {
                    let sessionData = JSON.parse(data);
                    console.log(sessionData);

                    if(typeof sessionData.email == "undefined" || sessionData.email == null) {
                        //User is not logged in
                        console.log("User is logged out");
                        window.location.href = "/auth";
                    }
                    else {
                        //User is logged in
                        console.log("User is logged in");
                        //window.location.href = "/dashboard";
                        //JSSPARouter.gotoPath('/dashboard');
                    }
                }
            });

            //window.location.href = "/dashboard"
            //JSSPARouter.gotoPath('/dashboard');
        });

        //Check to see if user is loggged in by trying to fetch user details
        $.ajax("/api/v1/session", {
            success: (data) => {
                let sessionData = JSON.parse(data);

                if(typeof sessionData.email == "undefined" || sessionData.email == null) {
                    //User is not logged in
                    console.log("User is logged out");
                    //window.location.href = "/auth"
                }
                else {
                    //User is logged in - route to dashboard
                    console.log("User is logged in");
                    $("#signin-btn").hide();
                    if(this.user == null) {
                        this.user = new User(this, sessionData);
                        this.user.fetchGitlabUserData().then(() => {
                            this.dashboard.render();
                        });
                    }
                    else {
                        this.dashboard.render();
                    }
                    
                    
                }
            }
        });

    }

    callback(type, transition, currentRoute) {
        //console.log("callback", type, transition, currentRoute);
    }

    createProject(type, transition, currentRoute) {
        if(type == "state.reached" && transition.name == "render") {
            console.log("createProject");
            this.user.createProject();
        }
    };

}

export { Application as default };