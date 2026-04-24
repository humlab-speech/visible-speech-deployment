<?php

$config = [
    'admin' => [
        'core:AdminPassword',
    ],

    'visp-example-users' => [
        'exampleauth:UserPass',

        'test1:test1pass' => [
            'uid' => ['test1'],
            'givenName' => ['Test'],
            'sn' => ['User One'],
            'mail' => ['testuser1@visp.local'],
            'eduPersonPrincipalName' => ['testuser1@visp.local'],
            'urn:oid:2.5.4.42' => ['Test'],
            'urn:oid:2.5.4.4' => ['User One'],
            'urn:oid:0.9.2342.19200300.100.1.3' => ['testuser1@visp.local'],
            'urn:oid:1.3.6.1.4.1.5923.1.1.1.6' => ['testuser1@visp.local'],
        ],

        'test2:test2pass' => [
            'uid' => ['test2'],
            'givenName' => ['Test'],
            'sn' => ['User Two'],
            'mail' => ['testuser2@visp.local'],
            'eduPersonPrincipalName' => ['testuser2@visp.local'],
            'urn:oid:2.5.4.42' => ['Test'],
            'urn:oid:2.5.4.4' => ['User Two'],
            'urn:oid:0.9.2342.19200300.100.1.3' => ['testuser2@visp.local'],
            'urn:oid:1.3.6.1.4.1.5923.1.1.1.6' => ['testuser2@visp.local'],
        ],

        'test3:test3pass' => [
            'uid' => ['test3'],
            'givenName' => ['Test'],
            'sn' => ['User Three'],
            'mail' => ['testuser3@visp.local'],
            'eduPersonPrincipalName' => ['testuser3@visp.local'],
            'urn:oid:2.5.4.42' => ['Test'],
            'urn:oid:2.5.4.4' => ['User Three'],
            'urn:oid:0.9.2342.19200300.100.1.3' => ['testuser3@visp.local'],
            'urn:oid:1.3.6.1.4.1.5923.1.1.1.6' => ['testuser3@visp.local'],
        ],
    ],
];
