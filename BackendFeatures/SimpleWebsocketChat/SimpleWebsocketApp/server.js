// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

'use strict';

// AWS-provided JWT verifier
const { JwtRsaVerifier } = require("aws-jwt-verify");

// X-ray for distributed tracing
var AWSXRay = require('aws-xray-sdk');
AWSXRay.config([AWSXRay.plugins.ECSPlugin]);

// AWS SDK with tracing
const AWS = AWSXRay.captureAWS(require('aws-sdk'));

const verifier = JwtRsaVerifier.create({
  issuer: process.env.ISSUER_ENDPOINT, // Get our custom issuer url from environment
  audience: "gamebackend", // set this to the expected "aud" claim to gamebackend
  jwksUri: process.env.ISSUER_ENDPOINT + "/.well-known/jwks.json", // Set the JWKS file path at issuer
  scope: ["guest", "authenticated"], // We accept guest and authenticated scope
});

// WEBSOCKET SERVER on 80

const WebSocket = require('ws');
const wss = new WebSocket.Server({ port: 80 });
const url = require('url');

wss.on('connection', (ws, req) => {
  const params = url.parse(req.url, true);
  // Iterate and print the parameters, we'll look for the token to validate
  Object.keys(params.query).forEach(key => {
    console.log(key + ': ' + params.query[key]);
  });

  ws.on('message', function message(data) {
    console.log('received: %s', data);
    ws.send("Received: " + data);
  });

  ws.send('something'); // send a message

});

// HEALTH CHECK SERVER on 8080

const express = require('express');

// Server constants
const PORT = 8080;
const HOST = '0.0.0.0';

// Server app
const app = express();

app.use(AWSXRay.express.openSegment('SimpleWebsocketChat-HealthCheck'));
// health check for root get
app.get('/', (req, res) => {
  res.status(200).json({ statusCode: 200, message: "OK" });
} );
app.use(AWSXRay.express.closeSegment());

// Setup app
app.listen(PORT, HOST, () => {
  console.log(`Running server on http://${HOST}:${PORT}`);
});
