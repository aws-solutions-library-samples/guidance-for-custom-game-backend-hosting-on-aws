# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

extends Node

# TODO: Add the login endpoint here
const login_endpoint = "https://YOURENDPOINT.execute-api.us-east-1.amazonaws.com/prod/"

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

	# Try linking Facebook ID to existing user
	# NOTE: You'll need to use a community Facebook integration such as https://github.com/DrMoriarty/godot-facebook
	#       Once you've logged in with Facebook, send the access_token and user_id here
	self.aws_game_sdk.link_facebook_id_to_current_user("AcceessTokenHere", "UserIdHere", self.on_link_facebook_id_response)
	
func on_link_facebook_id_response(user_info):
	print("Received Facebook ID linking info")
	print(user_info)

	# Let's now try to login with Facebook acccess token directly to access the same user
	self.aws_game_sdk.login_with_facebook_access_token("AccessTokenHere", "UserIdHere", self.on_login_with_facebook_response)
	
	
func on_login_with_facebook_response(user_info):
	print("Received Facebook ID login info")
	print(user_info)

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
