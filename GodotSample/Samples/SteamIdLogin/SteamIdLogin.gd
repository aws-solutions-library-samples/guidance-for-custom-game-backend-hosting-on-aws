# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

extends Node

# TODO: Add the login endpoint here
const login_endpoint = "https://YOURENDPOINTHERE/prod/"

var aws_game_sdk

# Called when the node enters the scene tree for the first time.
func _ready():
	
	# Get the SDK and Init
	self.aws_game_sdk = get_node("/root/AwsGameSdk")
	self.aws_game_sdk.init(self.login_endpoint, self.on_login_error)
	
	# Log in as new guest user first
	self.aws_game_sdk.login_as_new_guest_user(self.login_as_guest_callback)

# Called on any login or token refresh failures
func on_login_error(message):
	print("Login error: " + message)

# Receives a UserInfo object after successful guest login
func login_as_guest_callback(user_info):
	print("Received guest login info.")
	print(user_info)

	# Try linking Steam ID to existing user
	# NOTE: You need to use the Godot Steamworks SDK (https://godotsteam.com/) to integrate with Steam
	#       Use the GetAuthTicketForWebAPI to get the steam auth token (https://godotsteam.com/functions/users/#getauthticketforwebapi)
	self.aws_game_sdk.link_steam_id_to_current_user("YourTokenHere", self.on_link_steam_id_response)
	
func on_link_steam_id_response(user_info):
	print("Received steam ID linking info")
	print(user_info)

	# Let's now try to login with Steam token directly to access the same user
	# NOTE: You need to use the Godot Steamworks SDK (https://godotsteam.com/) to integrate with Steam
	#       Use the GetAuthTicketForWebAPI to get the steam auth token (https://godotsteam.com/functions/users/#getauthticketforwebapi)
	self.aws_game_sdk.login_with_steam_token("YourTokenHere", self.on_login_with_steam_response)
	
func on_login_with_steam_response(user_info):
	print("Received steam ID login info")
	print(user_info)

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
