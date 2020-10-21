<?php
class RStudioRouterInterface {
    private $app;
    /* enable when parent is a class
    function __construct($app) {
        $this->app = $app;
    }
    */

    /** 
    * Function: getRstudioSessions
    * This is really just acts as proxy towards the rstudio-router API.
    */
    function getSessions() {
        /*
        $httpClient = new GuzzleHttp\Client();
        $rstudioRouterApiRequest = "http://rstudio-router:80/api/sessions";
        try {
            $rstudioSessions = $httpClient->request('GET', $rstudioRouterApiRequest);
        }
        catch(Exception $e) {
            //addLog("Creating gitlab project:\nRequest: ".$gitlabApiRequest."\nPOST data:\n".print_r($postData, true)."\nResponse: ".$response);
            return $e;
        }
        */

        $rstudioRouterApiRequest = "http://rstudio-router:80/api/sessions";
        $rstudioSessions = httpRequest("GET", $rstudioRouterApiRequest);

        return $rstudioSessions->getBody();
    }

    /**
     * This function should probably be in the parent
     */
    function getGitlabProjectById($projectId) {
        foreach($_SESSION['gitlabProjects'] as $key => $proj) {
            if($proj->id == $projectId) {
                return $proj;
            }
        }
        return false;
    }

    /**
     * Function: getSession
     * Creates a container for a new session bases on the specified project. Or returns the currenly active session if it exists.
     */
    function getSessionOLD($projectId) {
        //WARNING: THIS ACCESS CHECK WILL ALWAYS PASS RIGHT NOW SINCE ITS NOT IMPLEMENTED
        /*
        if($this->app->checkUserProjectAccess($_SESSION['gitlabUser']->id, $projectId) == false) {
            //Should probably add some logging here about unathorized access being attempted
            return;
        }
        */
        $project = $this->getGitlabProjectById($projectId);
        if($project === false) {
            //No such project! TODO: Implement error handling here
            return false;
        }

        $httpClient = new GuzzleHttp\Client();
        //$rstudioRouterApiRequest = "http://rstudio-router:80/api/session/get";
        $rstudioRouterApiRequest = "http://rstudio-router:80/api/session/user/".$_SESSION['gitlabUser']->id."/project/".$projectId."/projectpath/".urlencode($project->path_with_namespace);
        
        try {
            $response = $httpClient->request('GET', $rstudioRouterApiRequest);
            $data = $response->getBody()->getContents();
            return $data;
        }
        catch(Exception $e) {
            return $e;
        }

        return $response->getBody();
    }

    /**
     * Function: getSession
     * Creates a container for a new session bases on the specified project. Or returns the currenly active session if it exists.
     */
    function getSession($projectId) {
        //WARNING: THIS ACCESS CHECK WILL ALWAYS PASS RIGHT NOW SINCE ITS NOT IMPLEMENTED
        /*
        if($this->app->checkUserProjectAccess($_SESSION['gitlabUser']->id, $projectId) == false) {
            //Should probably add some logging here about unathorized access being attempted
            return;
        }
        */
        $project = $this->getGitlabProjectById($projectId);
        if($project === false) {
            //No such project! TODO: Implement error handling here
            return false;
        }

        $httpClient = new GuzzleHttp\Client();
        //$rstudioRouterApiRequest = "http://rstudio-router:80/api/session/get";
        $rstudioRouterApiRequest = "http://rstudio-router:80/api/session/user";
        
        

        $postData = [
            'form_params' => [
                'gitlabUser' => json_encode($_SESSION['gitlabUser']),
                'gitlabProject' => json_encode($project),
                'rstudioSession' => $_COOKIE['rstudioSession']
            ]
        ];
        
        /*
        $postData = [
            'multipart' => [
                [
                    'name' => 'gitlabUser',
                    'contents' => json_encode($_SESSION['gitlabUser'])
                ],
                [
                    'name' => 'gitlabProject',
                    'contents' => json_encode($project)
                ]
            ]
        ];
        */

        try {
            $response = $httpClient->request('POST', $rstudioRouterApiRequest, $postData);
            $data = $response->getBody()->getContents();
            return $data;
        }
        catch(Exception $e) {
            return $e;
        }

        return $response->getBody();
    }

    function commitSession($rstudioSessionId) {
        //WARNING: THIS ACCESS CHECK WILL ALWAYS PASS RIGHT NOW SINCE ITS NOT IMPLEMENTED
        /*
        if($this->app->checkUserProjectAccess($_SESSION['gitlabUser']->id, $projectId) == false) {
            //Should probably add some logging here about unathorized access being attempted
            return;
        }
        */

        $httpClient = new GuzzleHttp\Client();
        //$rstudioRouterApiRequest = "http://rstudio-router:80/api/session/get";
        $rstudioRouterApiRequest = "http://rstudio-router:80/api/session/".$rstudioSessionId."/commit";
        
        try {
            $response = $httpClient->request('GET', $rstudioRouterApiRequest);
            $data = $response->getBody()->getContents();
            return $data;
        }
        catch(Exception $e) {
            return $e;
        }

        return $response->getBody();
    }

    function delSession($rstudioSessionId) {
        //WARNING: THIS ACCESS CHECK WILL ALWAYS PASS RIGHT NOW SINCE ITS NOT IMPLEMENTED
        /*
        if($this->app->checkUserProjectAccess($_SESSION['gitlabUser']->id, $projectId) == false) {
            //Should probably add some logging here about unathorized access being attempted
            return;
        }
        */

        $httpClient = new GuzzleHttp\Client();
        $rstudioRouterApiRequest = "http://rstudio-router:80/api/session/".$rstudioSessionId."/delete";
        
        try {
            $response = $httpClient->request('GET', $rstudioRouterApiRequest);
            $data = $response->getBody()->getContents();
            return $data;
        }
        catch(Exception $e) {
            return $e;
        }

        return $response->getBody();
    }
}

?>