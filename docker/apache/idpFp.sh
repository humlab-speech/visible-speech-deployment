curl --silent -k http://keycloak:8080/auth/realms/visp/protocol/saml/descriptor | grep -oPm1 "(?<=<ds:X509Certificate>)[^<]+" > cert.crt
certval=`cat cert.crt`

echo "-----BEGIN CERTIFICATE-----" > cert.crt
echo $certval >> cert.crt
echo "-----END CERTIFICATE-----" >> cert.crt

openssl x509 -in cert.crt -noout -fingerprint
                                                                                                                                                                   Back         ^◀ Prev Word
rm cert.crt