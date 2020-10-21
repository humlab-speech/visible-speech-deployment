class User {
    constructor(app, sessionData) {
        this.app = app;
        this.email = sessionData.email;
        this.displayName = sessionData.firstName+" "+sessionData.lastName;
        this.gitlabUser = null;
        this.gitlabAddress = "https://gitlab."+this.app.domain+"/";

        
    }

    fetchGitlabUserData() {

        return new Promise((resolve, reject) => {
            $.ajax("/api/v1/user", {
                method: "get",
                success: (data) => {
                    this.gitlabUser = JSON.parse(data);
                    console.log(this.gitlabUser);
                    resolve(this.gitlabUser);
                },
                error: (err) => {
                    console.log(err);
                    reject();
                }
            });
        });
        
    }


}

export { User as default };