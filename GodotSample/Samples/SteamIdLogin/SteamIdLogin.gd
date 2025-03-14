# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

extends Node

@onready var aws_games_sdk_auth = get_node("AWSGameSDKAuth")


# Called when the node enters the scene tree for the first time.
func _ready():

	#init sdk and setup signal listeners
	aws_games_sdk_auth.init()
	aws_games_sdk_auth.aws_login_success.connect(_on_login_success)
	aws_games_sdk_auth.aws_login_error.connect(_on_login_error)
	aws_games_sdk_auth.steam_link.conect(on_link_steam_id_response)
	aws_games_sdk_auth.steam_login.conect(on_login_with_steam_response)
	aws_games_sdk_auth.aws_sdk_error.connect(_on_aws_sdk_error)
	#login
	aws_games_sdk_auth.login()	


# Called on any login or token refresh failures
func _on_login_error(message):
	print("Login error: ", message)


func _on_aws_sdk_error(message):
	print("AWS SDK error: ", message)


# Receives a UserInfo object after successful guest login
func _on_login_success():
	print("Received guest login info.")
	print(aws_games_sdk_auth.user_info.to_string())

	# Try linking Steam ID to existing user
	# NOTE: You need to use the Godot Steamworks SDK (https://godotsteam.com/) to integrate with Steam
	#       Use the GetAuthTicketForWebAPI to get the steam auth token (https://godotsteam.com/functions/users/#getauthticketforwebapi)
	aws_games_sdk_auth.link_steam_id_to_current_user("YourTokenHere", self.on_link_steam_id_response)
	
func on_link_steam_id_response():
	print("Received steam ID linking info")
	print(aws_games_sdk_auth.user_info.to_string())

	# Let's now try to login with Steam token directly to access the same user
	# NOTE: You need to use the Godot Steamworks SDK (https://godotsteam.com/) to integrate with Steam
	#       Use the GetAuthTicketForWebAPI to get the steam auth token (https://godotsteam.com/functions/users/#getauthticketforwebapi)
	self.aws_game_sdk.login_with_steam_token("YourTokenHere", self.on_login_with_steam_response)
	
func on_login_with_steam_response():
	print("Received steam ID login info")
	print(aws_games_sdk_auth.user_info.to_string())

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
