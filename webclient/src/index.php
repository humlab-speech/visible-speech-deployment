<?php
$domain = getenv("HIRD_DOMAIN_NAME");
session_set_cookie_params(60*60*8, "/", ".".$domain);
session_start();
$_SESSION['projectName'] = getenv("PROJECT_NAME");
//print_r($_SERVER);

$shibHeadersFound = false;
$attributePrefix = "";
if(!empty($_SERVER['REDIRECT_Shib-Session-ID'])) {
    $shibHeadersFound = true;
    $attributePrefix = "REDIRECT_";
}
else if(!empty($_SERVER['Shib-Session-ID'])) {
    $shibHeadersFound = true;
    $attributePrefix = "";
}

if($shibHeadersFound) {
    $_SESSION['shibSessionId'] = $_SERVER[$attributePrefix.'Shib-Session-ID'];
    $_SESSION['shibSessionExpires'] = $_SERVER[$attributePrefix.'Shib-Session-Expires'];
    $_SESSION['shibSessionInactivity'] = $_SERVER[$attributePrefix.'Shib-Session-Inactivity'];
    $_SESSION['shibIdentityProvider'] = $_SERVER[$attributePrefix.'Shib-Identity-Provider'];

    $_SESSION['firstName'] = $_SERVER[$attributePrefix.'givenName'];
    $_SESSION['lastName'] = $_SERVER[$attributePrefix.'sn'];

    if(!empty($_SERVER[$attributePrefix.'email'])) {
        $_SESSION['email'] = $_SERVER[$attributePrefix.'email'];
    }
    else {
        $_SESSION['email'] = $_SERVER[$attributePrefix.'mail'];
    }
}
/*
if($_SERVER['REQUEST_URI'] == "/api/v1/session") {
    $output = [
        'FirstName' => $_SESSION['firstName'],
        'LastName' => $_SESSION['lastName'],
        'Email' => $_SESSION['email'],
    ];
    echo json_encode($output);
}
*/
include("index.html");
?>
