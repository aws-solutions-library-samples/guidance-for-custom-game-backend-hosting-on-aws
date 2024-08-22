# AWS Game Backend Framework Features: Friends Graph with Amazon Neptune Integration

- [Required preliminary setup](#required-preliminary-setup)
- [Deploying the Friends Graph with Amazon Neptune integration feature](#deploying-the-friends-graph-with-amazon-neptune-integration-feature)
- [Architecture](#architecture)
- [Solution overview](#solution-overview)
- [API Reference](#api-reference)

This backend feature integration shows how to deploy a backend service that interacts with Amazon Neptune to build a graph of players. Players can add friends and find new friends based on mutual relationships.

# Required preliminary setup

This backend feature **requires** that you have [deployed the Identity component](../../CustomIdentityComponent/README.md). Once that is done, **set** the `const ISSUER_ENDPOINT` in `BackendFeatures/FriendsGraphIntegration/bin/friends_graph_integration.ts` to the value of `IssuerEndpointUrl` found in the stack outputs of the _CustomIdentityComponentStack_. You can find it in the CloudFormation console, or in the terminal after deploying the identity component.

The issuer endpoint is a CloudFormation parameter and the value you set above sets the default value. It's also possible to set the endpoint later on as part of the CDK stack deployment using command line parameters (`--parameters IssuerEndpointUrl=<YOUR-ENDPOINT-HERE>`).

Make sure that you have Docker running before opening any terminals or Powershell as both the backend deployment as well as game server build process will use Docker. You're also expected to have all the tools listed in [Custom Identity Component Readme](../../CustomIdentityComponent/README.md#deploy-the-custom-identity-component) installed.

# Deploying the Friends Graph with Amazon Neptune integration feature

To deploy the component, follow the _Preliminary Setup_, and then run the following commands (Note: on **Windows** make sure to run in Powershell as **Administrator**):

1. Run `npm install` to install CDK app dependencies
2. Run `cdk deploy --all --no-previous-parameters` to deploy both the backend APIs as well as the Amazon Neptune resources CDK apps to your account. You will need to accept the deployment. This will take around 45 minutes to fully deploy.

# Architecture

The architecture diagram below shows the main steps of integration from the game engine to the backend and Amazon Neptune. See the main Readme of the project for details on how the Custom Identity Component is implemented.

**TBD**

# Solution overview

**TBD**

# API Reference

All API requests expect the `Authorization` header is set to the JWT value received when logging in. This is automatically done by the AWS Game SDK's for the different game engines when you call the POST and GET requests through their API's.

### GET /set-player

`GET /set-player`

**Parameters**

None.

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `{ "v[d41d8cd98f00b204e9800998ecf8427e]" }` | Returns the vertex node of the player created or updated. |
> | `500`         |  `"Unexpected error."` | |

### GET /get-player

`GET /get-player`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `player_id`   |  Yes       | User ID.  |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `[ { "v[d41d8cd98f00b204e9800998ecf8427e]" } ]` | List of vertices matching the player ID. |
> | `500`         |  `"Unexpected error."` | |

### GET /set-friend

`GET /set-friend`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `friend_id`   |  Yes       | User ID to add to friends list. |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `[ "e[e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855]" ]` | List of edges connecting from the current player to the friend ID. |
> | `500`         |  `"Unexpected error."` | |

### GET /delete-friend

`GET /delete-friend`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `friend_id`   |  Yes       | User ID to remove from friends list.  |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `"Success"` | |
> | `500`         |  `"Unexpected error."` | |

### GET /get-friends

`GET /get-friends`

**Parameters**

> | name      |  required | description                                                                    |
> |-----------|-----------|--------------------------------------------------------------------------------|
> | `player_id`   |  No       | User ID. Default value is current player ID. |
> | `dir` | No | Direction of relationship. Valid values are `in`, `out`, or `new`. Set to `in` to see users who added `player_id` to their friends list. Set to `out` to see users who have been added by `player_id` to their friends list. Set to `new` to find new friend suggestions. Default value is `out`. |
> | `max` | No | Number of user vertices to return. Default value is `10`. |

**Responses**

> | http code     | response                                                            | description |
> |---------------|---------------------------------------------------------------------|---|
> | `200`         | `[ "v[d41d8cd98f00b204e9800998ecf8427e]" ]` or `[ { "v[d41d8cd98f00b204e9800998ecf8427e]" : 3 } ]` | List of user vertices. If `dir` is set to `new`, the number of mutual friends is also returned. |
> | `500`         |  `"Unexpected error."` | |
