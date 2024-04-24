TODO

NOTES:

* Explain the HTTPS (wss) on the CloudFront level and how you would use your own domain, certificates, and HTTPS on the ALB as well


COMMANDS:
curl https://abcdef.execute-api.us-east-1.amazonaws.com/prod/login-as-guest
websocat 'wss://abcdef.cloudfront.net/?auth_token=eyYOURTOKEN'

