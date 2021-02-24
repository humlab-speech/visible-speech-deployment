curl --silent -k https://idp.localtest.me/auth/realms/humlab-speech/protocol/saml/descriptor | grep -oPm1 "(?<=<ds:X509Certificate>)[^<]+" > cert.crt
certval=`cat cert.crt`

echo "-----BEGIN CERTIFICATE-----" > cert.crt
echo $certval >> cert.crt
echo "-----END CERTIFICATE-----" >> cert.crt

openssl x509 -in cert.crt -noout -fingerprint
