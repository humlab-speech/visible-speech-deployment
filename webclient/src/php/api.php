<?php
require 'vendor/autoload.php';
require 'RstudioRouterInterface.class.php';

$domain = getenv("HIRD_DOMAIN_NAME");
session_set_cookie_params(60*60*8, "/", ".".$domain);
session_start();


//$gitlabUser = null;
$gitlabAddress = "http://gitlab:80";
$gitlabAccessToken = getenv("GIT_API_ACCESS_TOKEN");
$hirdApiAccessToken = getenv("HIRD_API_ACCESS_TOKEN");


$reqPath = $_SERVER['REQUEST_URI'];
$reqMethod = $_SERVER['REQUEST_METHOD'];

$rstudioRouterInterface = new RStudioRouterInterface();

if($reqMethod == "GET") {
    switch($reqPath) {
        case "/api/v1/magick":
            $out = magick();
        break;
        case "/api/v1/user":
            $out = getGitlabUser();
        break;
        case "/api/v1/session":
            $out = getUserSessionAttributes();
        break;
        case "/api/v1/user/project":
            $out = getGitlabUserProjects();
        break;
        /*
        case "/api/v1/rstudio/sessions":
            $out = $rstudioRouterInterface->getSessions();
        break;
        */
    }

    echo $out;
}

function httpRequest($method = "GET", $url, $options = []) {

    $httpClient = new GuzzleHttp\Client();

    $exception = false;
    $response = "";

    if(strtolower($method) == "post") {
        try {
            $response = $httpClient->request($method, $url, $options);
        }
        catch(Exception $e) {
            $exception = $e;
        }
    }

    if(strtolower($method) == "get") {
        try {
            $response = $httpClient->request($method, $url);
        }
        catch(Exception $e) {
            $exception = $e;
        }
    }

    if($exception !== false) {
        //This contains the gitlab root key - very sensitive info - in production this shouldn't be outputted at all, but just redacting the key for now to make debugging easier in development
        $exceptionOutput = preg_replace("/private_token=.[A-Za-z0-9_-]*/", "/private_token=REDACTED", $exception);
        return $exceptionOutput;
    }

    return $response;
}

function magick() {
    print_r($_SESSION);
}

//HERE BE DANGER - This section needs to have some security applied
if($reqMethod == "POST") {
    switch($reqPath) {
        case "/api/v1/user":
            //TODO: How do we make sure this is a valid user which should be made? We only have the client's word for it at this point - which can't be trusted!
            $out = createGitlabUser();
        break;
        case "/api/v1/user/project":
            //TODO: Perhaps verify that this user has the right to create a new project?
            $out = createGitlabProject();
        break;
        case "/api/v1/rstudio/session/please":
            //FIXME: This should probably be a GET, not a POST
            //$out = "{ 'status': 'maybe ok' }";
            $out = $rstudioRouterInterface->getSession($_POST['projectId']);
        break;
        case "/api/v1/rstudio/save":
            $out = $rstudioRouterInterface->commitSession($_POST['rstudioSession']);
        break;
        case "/api/v1/rstudio/close":
            $out = $rstudioRouterInterface->delSession($_POST['rstudioSession']);
        break;
        
    }
    echo $out;
}

if($reqMethod == "DELETE") {
    switch($reqPath) {
        
    }
}

//print_r($_SERVER);

function checkUserProjectAccess($userId, $projectId) {
    //FIXME: THIS (DESPERATELY) NEEDS IMPLEMENTING!!!
    return true; //This is not 'great' in production
}

function getGitLabUsername($email) {
    return str_replace("@", "_at_", $email);
}

function addLog($msg) {
    file_put_contents("/var/log/hird-api.log", "[".date("Y-m-d H:i:s")."]\n".$msg."\n", FILE_APPEND);
}

function getUserSessionAttributes() {
    /*
    $output = [
        'FirstName' => $_SERVER['givenName'],
        'LastName' => $_SERVER['sn'],
        'FullName' => $_SERVER['givenName']." ".$_SERVER['sn'],
        'Email' => $_SERVER['email'],
        'GitlabUsername' => getGitLabUsername($_SERVER['email'])
    ];
    */

    $output = [
        'firstName' => $_SESSION['firstName'],
        'lastName' => $_SESSION['lastName'],
        'fullName' => $_SESSION['firstName']." ".$_SESSION['lastName'],
        'email' => $_SESSION['email'],
        'gitlabUsername' => getGitLabUsername($_SESSION['email'])
    ];

    //$output = $_SESSION;

    return json_encode($output);
}

function createGitlabUser() {
    global $gitlabAddress, $gitlabAccessToken;
    
    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $gitlabApiRequest = $gitlabAddress."/api/v4/users?username=".$gitlabUsername."&private_token=".$gitlabAccessToken;

    $options = [
            'form_params' => [
                'email' => $_SESSION['email'],
                'name' => $_SESSION['firstName']." ".$_SESSION['lastName'],
                'username' => $gitlabUsername,
                'force_random_password' => '1',
                'reset_password' => 'false',
                'skip_confirmation' => true,
                'provider' => $_SESSION['Shib-Identity-Provider']
        ]
    ];

    $response = httpRequest("POST", $gitlabApiRequest, $options); 
    
    if(is_string($response)) {
        echo "Response was string!\n";
        echo $response;
    }

    addLog("Creating gitlab user:\nRequest: ".$gitlabApiRequest."\nPOST data:\n".print_r($postData, true)."\nResponse: ".$data);

    if($response->getStatusCode() == 409) {
        //User already exists
        return $response->getBody()->getContents();
    }

    if($response->getStatusCode() == 201) {
        //$data = $response->getBody()->getContents();
        //return $data;

        return getGitlabUser();
    }
    else {
        return "createGitlabUser: HTTP non-201 code (".$response->getStatusCode().")";
    }
}

function getGitlabUser() {
    global $gitlabAddress, $gitlabAccessToken, $gitlabUser;
    //Gets User info from Gitlab for currently logged in user

    /*
    $httpClient = new GuzzleHttp\Client();
    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $gitlabApiRequest = $gitlabAddress."/api/v4/users?username=".$gitlabUsername."&private_token=".$gitlabAccessToken;
    //$data = file_get_contents($gitlabApiRequest);
    addLog("Getting gitlab user:\nRequest: ".$gitlabApiRequest."\nResponse: ");
    try {
        $response = $httpClient->request('GET', $gitlabAddress."/api/v4/users", [
            'query' => [
                'username' => $gitlabUsername,
                'private_token' => $gitlabAccessToken
            ]
        ]);
    }
    catch(Exception $e) {
        return $e;
    }
    */

    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $gitlabApiRequest = $gitlabAddress."/api/v4/users?username=".$gitlabUsername."&private_token=".$gitlabAccessToken;

    $response = httpRequest("GET", $gitlabApiRequest);


    if($response->getStatusCode() == 200) {
        $userListJson = $response->getBody()->getContents();
        $userList = json_decode($userListJson);
        if(empty($userList)) {
            //User does not exist
            return createGitlabUser();
        }
        else {
            $_SESSION['gitlabUser'] = $userList[0];
            return json_encode($userList[0]);
        }
    }
    else {
        return "getGitlabUser: HTTP non-200 code";
    }
}

function getGitlabUserProjects() {
    global $gitlabAddress, $gitlabAccessToken, $hirdApiAccessToken;
    //Gets Gitlab projects for currently logged in user

    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $gitlabApiRequest = $gitlabAddress."/api/v4/users/".$gitlabUsername."/projects?private_token=".$gitlabAccessToken;
    $response = httpRequest("GET", $gitlabApiRequest);
    $projects = json_decode($response->getBody());
    $_SESSION['gitlabProjects'] = $projects;


    
    //$data = file_get_contents($gitlabApiRequest);
    addLog("Getting gitlab user projects:\nRequest: ".$gitlabApiRequest."\nResponse: ".$response->getBody());

    /*
    //Also check if any of these projects have an active running session in the rstudio-router via its API
    //$httpClient = new GuzzleHttp\Client();
    $rstudioRouterApiRequest = "http://rstudio-router:80/api/sessions/".$_SESSION['gitlabUser']->id;
    try {
        //add header HIRD_API_ACCESS_TOKEN=$hirdApiAccessToken
        $headers = ['HIRD_API_ACCESS_TOKEN' => $hirdApiAccessToken];

        $rstudioSessions = $httpClient->request('GET', $rstudioRouterApiRequest, [
            'headers' => [
                'User-Agent' => 'hird-api/1.0',
                'Accept'     => 'application/json',
                'hird_api_access_token' => $hirdApiAccessToken
            ]
        ]);
    }
    catch(Exception $e) {
        //addLog("Creating gitlab project:\nRequest: ".$gitlabApiRequest."\nPOST data:\n".print_r($postData, true)."\nResponse: ".$response);
        return $e;
    }
    */


    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $rstudioRouterApiRequest = "http://rstudio-router:80/api/sessions/".$_SESSION['gitlabUser']->id;
    $rstudioSessions = httpRequest("GET", $rstudioRouterApiRequest, [
        'headers' => [
            'User-Agent' => 'hird-api/1.0',
            'Accept'     => 'application/json',
            'hird_api_access_token' => $hirdApiAccessToken
        ]
    ]);

    if(is_string($rstudioSessions)) {
        echo "Response was string!\n";
        echo $rstudioSessions;
    }

    $sessions = json_decode($rstudioSessions->getBody());

    foreach($projects as $key => $project) {
        $projects[$key]->sessions = array();
        
        foreach($sessions as $sesKey => $session) {

            if($session->projectId == $project->id) {
                $projects[$key]->sessions []= $session;
            }

        }
    }
    

    //Call to http://rstudio-router:80/api/sessions
    return json_encode($projects);
    //return $response->getBody();
}



function createGitlabProject() {
    global $gitlabAddress, $gitlabAccessToken;

    //First, create user impersonation token - actually, don't think we need this
    /*
    $response = getAllGitlabUserImpersonationTokens();
    if(empty(json_decode($response))) {
        $token = createGitlabUserImpersonationToken();
    }
    */

    /*
    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $gitlabApiRequest = $gitlabAddress."/api/v4/projects/user/".$_SESSION['gitlabUser']->id."?private_token=".$gitlabAccessToken;
    $httpClient = new GuzzleHttp\Client();
    $postData = array(
        'name' => $_POST['name']
        //'import_url' => $gitlabAddress.'/root/emu-db-template.git'
    );
    try {
        $response = $httpClient->request('POST', $gitlabApiRequest, [
            'form_params' => $postData
        ]);
    }
    catch(Exception $e) {
        //addLog("Creating gitlab project:\nRequest: ".$gitlabApiRequest."\nPOST data:\n".print_r($postData, true)."\nResponse: ".$response);
        return $e;
    }
    */



    $gitlabUsername = getGitLabUsername($_SESSION['email']);
    $gitlabApiRequest = $gitlabAddress."/api/v4/projects/user/".$_SESSION['gitlabUser']->id."?private_token=".$gitlabAccessToken;
    $postData = array(
        'name' => $_POST['name']
        //'import_url' => $gitlabAddress.'/root/emu-db-template.git'
    );
    $response = httpRequest("POST", $gitlabApiRequest, [
        'form_params' => $postData
    ]);

    if(!is_string($response)) {
        $responseText = $response->getBody();
    }
    else {
        $responseText = $response;
    }

    addLog("Creating gitlab project:\nRequest: ".$gitlabApiRequest."\nPOST data:\n".print_r($postData, true)."\nResponse: ".$responseText);

    return $responseText;
}

/*
function getGitlabUserImpersonationToken($impersonation_token_id) {
    global $gitlabAddress, $gitlabAccessToken, $gitlabUser;

    $gitlabUsername = getGitLabUsername($_SESSION['email']);

    $gitlabApiRequest = $gitlabAddress."/api/v4/users/".$_SESSION['gitlabUser']->id."/impersonation_tokens/".$impersonation_token_id."?private_token=".$gitlabAccessToken;
    $data = file_get_contents($gitlabApiRequest);
    addLog("Getting gitlab user impersonation token:\nRequest: ".$gitlabApiRequest."\nResponse: ".$data);

    return json_encode($data);
}

function getAllGitlabUserImpersonationTokens() {
    global $gitlabAddress, $gitlabAccessToken, $gitlabUser;

    $gitlabUsername = getGitLabUsername($_SESSION['email']);

    $gitlabApiRequest = $gitlabAddress."/api/v4/users/".$_SESSION['gitlabUser']->id."/impersonation_tokens?private_token=".$gitlabAccessToken;
    $data = file_get_contents($gitlabApiRequest);
    addLog("Getting all gitlab user impersonation tokens:\nRequest: ".$gitlabApiRequest."\nResponse: ".$data);

    return $data;
}

function createGitlabUserImpersonationToken() {
    global $gitlabAddress, $gitlabAccessToken, $gitlabUser;

    $gitlabUsername = getGitLabUsername($_SESSION['email']);

    $gitlabApiRequest = $gitlabAddress."/api/v4/users/".$_SESSION['gitlabUser']->id."/impersonation_tokens?private_token=".$gitlabAccessToken."&name=".$gitlabUsername."-impersonation-token&user_id=".$_SESSION['gitlabUser']->id."&scopes[]=api";
    //scopes[]=api
    
    
    $httpClient = new GuzzleHttp\Client();

    try {
        $response = $httpClient->request('POST', $gitlabApiRequest);
    }
    catch(Exception $e) {
        return $e;
    }

    addLog("Creating gitlab user impersonation token:\nRequest: ".$gitlabApiRequest."\nPOST data:\n".print_r($postData, true)."\nResponse: ".$response->getBody()->getContents());

    if($response->getStatusCode() == 200) {
        
    }

    return $response->getBody()->getContents();
}
*/
//curl -d "email=whatevs@umu.se&name=User One&username=user1&force_random_password=1&reset_password=false" http://gitlab:80/api/v4/users?private_token=57Cyg_rX7UEfDMPi7tWo

//curl http://gitlab:80/api/v4/users?username=johan.von.boer&private_token=57Cyg_rX7UEfDMPi7tWo

//Gitlab username: johan.von.boer_at_umu.se

?>