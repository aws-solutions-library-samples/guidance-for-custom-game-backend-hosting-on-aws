# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

extends Node2D

@onready var aws_games_sdk_auth = get_node("AWSGameSDKAuth")
@onready var aws_games_sdk_backend = get_node("AWSGameSDKBackend")

var logged_in: bool = false
var actions: Array
var current_action: String

# Called when the node enters the scene tree for the first time.
func _ready():
	# Get the SDK and Init
	aws_games_sdk_auth.init()
	aws_games_sdk_auth.aws_login_success.connect(_on_login_success)
	aws_games_sdk_auth.aws_login_error.connect(_on_login_error)
	aws_games_sdk_auth.aws_sdk_error.connect(_on_aws_sdk_error)
	aws_games_sdk_backend.aws_backend_request_successful.connect(_on_backend_request_success)
	aws_games_sdk_backend.aws_sdk_error.connect(_on_aws_sdk_error)
	print("calling login")
	aws_games_sdk_auth.login()	

# Called on any login or token refresh failures
func _on_login_error(message):
	print("Login error: " + message)


# Receives a UserInfo object after successful login
func _on_login_success():
	print("Received login success")
	logged_in = true
	actions = ['setdata', 'getdata']
	current_action = actions.pop_front()
	#you can inspect the user_info with this line
	#print(aws_games_sdk_auth.user_info.to_string())
	# Try setting player data


func _on_backend_request_success():
	print("Backend request successful")
	print("Data returned from action: ", aws_games_sdk_backend.get_response_data())
	if len(actions) > 0:
		current_action = actions.pop_front()

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	if logged_in and current_action != "":
		if current_action == "setdata":
			current_action = ""
			print("setting player data")
			aws_games_sdk_backend.backend_set_request(aws_games_sdk_auth.get_auth_token(), {"player_name" : "John Doe"})
		if current_action == "getdata":
			current_action = ""
			print("getting player data")
			aws_games_sdk_backend.backend_get_request(aws_games_sdk_auth.get_auth_token())
	return

func _on_aws_sdk_error(error_text):
	print("Error received from AWS SDK: ", error_text)
