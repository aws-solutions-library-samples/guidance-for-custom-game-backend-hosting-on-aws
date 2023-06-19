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

app.use(AWSXRay.express.openSegment('NodeJsFargateApi-PlayerData'));

// Set player data to DynamoDB
app.get('/set-player-data', async (req, res) => {

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

  // Set the player data and send response
  try {
    // Set the player data to DynamoDB
    const dynamoDB = new AWS.DynamoDB.DocumentClient();
    const params = {
      TableName: process.env.PLAYER_DATA_TABLE_NAME,
      Item: {
        UserID: payload.sub,
        Playername: req.query.player_name,
      }
    }
    dynamoDB.put(params, (err, data) => {
      if (err) {
        console.log(err);
        res.status(500).json({ statusCode: 500, message: "Something went wrong setting player data" });
      } else {
        res.status(200).json({ statusCode: 200, message: "Player data set successfully" });
      }
    });
  }
  catch (err) {
    console.log(err);
    res.status(500).json({ statusCode: 500, message: "Something went wrong" });
  }
});

// Get player data from DynamoDB
app.get('/get-player-data', async (req, res) => {

  // Validate token first
  var payload = null;
  try {
    payload = await verifier.verify(req.header("Authorization"));
    console.log("Token is valid");//. Payload:", payload);
  } catch (err) {
    console.log(err);
    res.status(403).json({ statusCode: 403, message: "Token not valid" });
    return;
  }

  // Set the player data and send response
  try {
    // Get player data from DynamoDB
    const dynamoDB = new AWS.DynamoDB.DocumentClient();
    const params = {
      TableName: process.env.PLAYER_DATA_TABLE_NAME,
      Key: {
        UserID: payload.sub
      }
    }
    dynamoDB.get(params, (err, data) => {
      if (err) {
        console.log(err);
        res.status(500).json({ statusCode: 500, message: "Something went wrong getting player data" });
      } else {
        res.status(200).json({ statusCode: 200, message: "Player data retrieved successfully", player_data: data.Item });
      }
    });
  }
  catch (err) { 
    console.log(err);
    res.status(500).json({ statusCode: 500, message: "Something went wrong" });
  }
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