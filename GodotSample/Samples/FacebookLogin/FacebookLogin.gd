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
	aws_games_sdk_auth.fb_link.conect(on_link_facebook_id_response)
	aws_games_sdk_auth.fb_login.conect(on_login_with_facebook_response)
	aws_games_sdk_auth.aws_sdk_error.connect(_on_aws_sdk_error)
	#login
	aws_games_sdk_auth.login()	
	# Get the SDK and Init
	self.aws_game_sdk = get_node("/root/AwsGameSdk")
	self.aws_game_sdk.init(self.login_endpoint, self.on_login_error)
	
	# Log in as new guest user first
	self.aws_game_sdk.login_as_new_guest_user(self.login_as_guest_callback)

# Called on any login or token refresh failures
func _on_login_error(message):
	print("Login error: " + message)


func _on_aws_sdk_error(message):
	print("AWS SDK error: ", message)
	

# Receives a UserInfo object after successful guest login
func _on_login_success():
	print("Received guest login info.")
	print(aws_games_sdk_auth.user_info)

	# Try linking Facebook ID to existing user
	# NOTE: You'll need to use a community Facebook integration such as https://github.com/DrMoriarty/godot-facebook
	#       Once you've logged in with Facebook, send the access_token and user_id here
	self.aws_game_sdk.link_facebook_id_to_current_user("AcceessTokenHere", "UserIdHere", self.on_link_facebook_id_response)
	
func on_link_facebook_id_response():
	print("Received Facebook ID linking info")
	print(aws_games_sdk_auth.user_info.to_string())

	# Let's now try to login with Facebook acccess token directly to access the same user
	self.aws_game_sdk.login_with_facebook_access_token("AccessTokenHere", "UserIdHere", self.on_login_with_facebook_response)
	
	
func on_login_with_facebook_response():
	print("Received Facebook ID login info")
	print(aws_games_sdk_auth.user_info.to_string())

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
