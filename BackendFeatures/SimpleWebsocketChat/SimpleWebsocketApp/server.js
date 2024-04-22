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

const express = require('express');

// Server constants
const PORT = 80;
const HOST = '0.0.0.0';

// Server app
const app = express();

app.use(AWSXRay.express.openSegment('SimpleWebsocketChat-Connect'));

// TODO: Replace with Websocket logic
app.get('/connect', async (req, res) => {

  // Validate token first
  var payload = null;
  try {
    payload = await verifier.verify(req.header("Authorization"));
    console.log("Token is valid"); //. Payload:", payload);
  } catch (err) {
    console.log(err);
    res.status(403).json({ statusCode: 403, message: "Token not valid" });
    return;
  }

  // TODO: Replace with Redis Pub/Sub and Websockets

});

app.use(AWSXRay.express.closeSegment());

// health check for root get
app.get('/', (req, res) => {
  res.status(200).json({ statusCode: 200, message: "OK" });
} );

// Setup app
app.listen(PORT, HOST, () => {
  console.log(`Running server on http://${HOST}:${PORT}`);
});