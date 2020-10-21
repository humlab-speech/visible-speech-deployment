"use strict";

import 'babel-polyfill';
import "../index.html";
import '../stylesheets/style.scss';
//import "../assets/icons/favicon.ico";
//import "../assets/icons/android-chrome-192x192.png";
//import "../site.webmanifest";
import "js-spa-router";
import "../assets/loading.gif";

import Application from "./Application.class";


let renderDashboard = async (type, transition, currentRoute) => {
    
    if(type == "state.reached" && transition.name == "render") {
        console.log("renderDashboard");
        //console.log(type, transition, currentRoute);
        
        //need to redirect to auth
        //window.location = "http://localhost/auth/realms/hird/account";
        //window.location = "http://localhost/auth/realms/hird/clients/hird-main"
        /*
        user.fetch().then((userData) => {
            dash = new Dashboard(user);
            $("#signin-btn").hide();
        });
        */

        let loadingPromises = [];

        let p1 = new Promise((resolve, reject) => {
            $.ajax("/api/v1/session", {
                dataType: "json",
                success: (data) => {
                    console.log(data);
                    app.user = new User();
                    app.user.email = data.Email;
                    app.user.displayName = data.FirstName+" "+data.LastName;
                    app.user.fetchGitlabUserData();
                    //JSSPARouter.gotoPath('/dashboard');
                    resolve(data);
                }
            });
        });

        loadingPromises.push(p1);

        let p2 = new Promise((resolve, reject) => {
            $.ajax("/api/v1/user", {
                dataType: "json",
                success: (data) => {
                    console.log(data);
                    resolve(data);
                },
                error: () => {
                    reject();
                }
            });
        });

        loadingPromises.push(p2);

        await Promise.all(loadingPromises);
        
        

        $.ajax("/api/v1/user/project", {
            dataType: "json",
            success: (projects) => {
                console.log(projects);
                app.dashboard.renderProjects(projects);
            }
        });

        
       app.dashboard = new Dashboard(app);
       $("#signin-btn").hide();
    }
};


let ws = null;

jQuery(function() {
    console.log("ready");
    const app = new Application();

    /*
    //This will open the connection*
    ws = new WebSocket("wss://localtest.me:3000");
    //ws = new WebSocket("wss://localtest.me");
    $("#magic-btn").on("click", () => {
        console.log("Sending ws ping");
        ws.send("Ping");
    });
    
            
    //Log the messages that are returned from the server
    ws.onmessage = function (e) {
        console.log("From Server:"+ e.data);
    };
    */
});


