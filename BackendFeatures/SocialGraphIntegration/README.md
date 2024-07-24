# AWS Game Backend Framework Features: Social Graph with Amazon Neptune Integration

- [Required preliminary setup](#required-preliminary-setup)
- [Deploying the Social Graph with Amazon Neptune integration feature](#deploying-the-social-graph-with-amazon-neptune-integration-feature)
- [Architecture](#architecture)
- [Solution overview](#solution-overview)
- [API Reference](#api-reference)

This backend feature integration shows how to deploy a backend service that interacts with Amazon Neptune to build a graph of players. Players can add friends and find new friends based on mutual relationships.

# Required preliminary setup

This backend feature **requires** that you have [deployed the Identity component](../../CustomIdentityComponent/README.md). Once that is done, **set** the `const ISSUER_ENDPOINT` in `BackendFeatures/AmazonGameLiftIntegration/bin/amazon_gamelift_integration.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

The issuer endpoint is a CloudFormation parameter and the value you set above sets the default value. It's also possible to set the endpoint later on as part of the CDK stack deployment using command line parameters (`--parameters IssuerEndpointUrl=<YOUR-ENDPOINT-HERE>`).

Make sure that you have Docker running before opening any terminals or Powershell as both the backend deployment as well as game server build process will use Docker. You're also expected to have all the tools listed in [Custom Identity Component Readme](../../CustomIdentityComponent/README.md#deploy-the-custom-identity-component) installed.

# Deploying the Social Graph with Amazon Neptune integration feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands (Note: on **Windows** make sure to run in Powershell as **Administrator**):

1. Run `npm install` to install CDK app dependencies
2. Run `cdk deploy --all --no-previous-parameters` to deploy both the backend APIs as well as the Amazon Neptune resources CDK apps to your account. You will need to accept the deployment. This will take around 45 minutes to fully deploy.

# Architecture

The architecture diagram below shows the main steps of integration from the game engine to the backend and Amazon Neptune. See the main Readme of the project for details on how the Custom Identity Component is implemented.

**TBD**

# Solution overview

**TBD**

# API Reference

<!-- All API requests expect the `Authorization` header is set to the JWT value received when logging in. This is automatically done by the AWS Game SDK's for the different game engines when you call the POST and GET requests through their API's. -->

### POST /player

`POST /player`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `body`   |  Yes       | The body of the POST request. Must be in JSON format with player ID. Example: `{ "id": "d41d8cd98f00b204e9800998ecf8427e" }`  |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `{ "v[d41d8cd98f00b204e9800998ecf8427e]" }` | Vertex |
> | `500`         |  `"Unexpected error"` | |

### POST /friend

`POST /friend`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `body`   |  Yes       | The body of the POST request. Must be in JSON format with player ID and friend ID. Example: `{ "from_id": "d41d8cd98f00b204e9800998ecf8427e", "to_id": "e7248fce8990089e402b00f89dc8d14d" }`  |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `[ { "[e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855]" } ]` | List of Edge IDs |
> | `500`         |  `"Unexpected error"` | |

### GET /player

`GET /player`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `id`   |  Yes       | The player ID. |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `[ "v[d41d8cd98f00b204e9800998ecf8427e]" ]` | List of Vertices |
> | `500`         |  `"Unexpected error."` | |

### GET /friends

`GET /friends`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `id`   |  Yes       | The player ID. |
> | `dir` | No | Direction of relationship. Valid values are `in`, `out`, or `new`. Set to `new` to find friend suggestions. Default value is `out`. |
> | `max` | No | Integer type. Number of vertices to return. Default value is `10`. |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `[ "v[d41d8cd98f00b204e9800998ecf8427e]" ]` or `[ { "v[d41d8cd98f00b204e9800998ecf8427e]" : 3 } ]` | List of Vertices. If `dir` is set to `new`, the number of mutual relationships is also returned. |
> | `500`         |  `"Unexpected error."` | |

### DELETE /player

`DELETE /player`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `body`   |  Yes       | The body of the POST request. Must be in JSON format with player ID. Example: `{ "id": "d41d8cd98f00b204e9800998ecf8427e" }`  |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `"Success"` | |
> | `500`         |  `"Unexpected error"` | |

### DELETE /friend

`DELETE /friend`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `body`   |  Yes       | The body of the POST request. Must be in JSON format with edge ID. Example: `{ "id": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" }`  |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `"Success"` | |
> | `500`         |  `"Unexpected error"` | 
