# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
extends Node
class_name AWSGameSDKAuth

signal aws_login_success
signal aws_login_error
signal aws_sdk_error
signal steam_link
signal steam_login
signal fb_link
signal fb_login
signal apple_link
signal apple_login
signal goog_link
signal goog_login

#only used for internal signalling for the SDK
signal _new_login_succesful

@export var login_endpoint: String = "" # Endpoint for custom identity component

const UserInfo = preload("user_info.gd")

var user_info: UserInfo = null # User info for the logged in user
var refresh_timer: Timer #used to check on refresh for access token
var http_request: HTTPRequest
var error_string: String = ""
var new_user_login: bool = false

var linking: Dictionary = { "steam_link": false, "steam_login": false, "fb_link": false, 
	"fb_login": false, "apple_link": false, "apple_login": false, 
	"goog_link": false, "goog_login": false}

# Called when the node enters the scene tree for the first time.
func _ready():
	refresh_timer = Timer.new()
	refresh_timer.one_shot = true
	add_child(refresh_timer)
	refresh_timer.timeout.connect(_on_timer_timeout)
	pass


func _save_login_data():
	var file = FileAccess.open("user://save_game.dat", FileAccess.WRITE)
	file.store_pascal_string(user_info.user_id)
	file.store_pascal_string(user_info.guest_secret)
	file.close()
	file = null

	
func _load_login_data():
	var file = FileAccess.open("user://save_game.dat", FileAccess.READ)
	if file == null:
		return
	if file.get_length() == 0:
		file.close()
		return null;
	user_info.user_id = file.get_pascal_string()
	user_info.guest_secret = file.get_pascal_string()
	file.close()

func get_auth_token() -> String:
	if user_info != null:
		return user_info.auth_token
	else:
		return ""


func login():
	if user_info.user_id == "" or user_info.guest_secret == "":
		_login_as_new_guest()
	else:
		_login_as_guest()
		

func _on_timer_timeout():
	login_with_refresh_token(user_info.refresh_token)


func init():
	_new_login_succesful.connect(_save_login_data)
	user_info = UserInfo.new()
	_load_login_data()
	print("AWS Game SDK initialized")
	

func _make_auth_http_request(url: String, method: HTTPClient.Method = HTTPClient.METHOD_GET, form_data: Dictionary = {}) -> void:
	# Create an HTTP request node and connect its completion signal.
	http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(_auth_request_completed)
	
	var error = http_request.request(url)
	#send signal on failure
	if error != OK:
		print(error)
		aws_login_error.emit("Error making request to login endpoint")


func _auth_request_completed(result, response_code, headers, body):
	http_request.queue_free()
	var json_string = body.get_string_from_utf8() # Retrieve data
	var json = JSON.new()
	var error = json.parse(json_string)
	
	# trigger error if we didn't get a proper response code
	if(response_code >= 400):
		error_string = json_string
		return
	
	# Check we got no error
	if error == OK:
		var data_received = json.data
		# Check that we got a user_id (valid response)
		if(!data_received.has("user_id")):
			error_string = json_string
			return
		
		# We got valid response, let's parse values to UserInfo object
		if(user_info == null):
			user_info = UserInfo.new()
		user_info.user_id = data_received["user_id"]
		if(data_received.has("guest_secret")):
			user_info.guest_secret = data_received["guest_secret"]
		if(data_received.has("auth_token")):
			user_info.auth_token = data_received["auth_token"]
		if(data_received.has("refresh_token")):
			user_info.refresh_token = data_received["refresh_token"]
		if(data_received.has("auth_token_expires_in")):
			user_info.auth_token_expires_in = data_received["auth_token_expires_in"]
		if(data_received.has("refresh_token_expires_in")):
			user_info.refresh_token_expires_in = data_received["refresh_token_expires_in"]
		if(data_received.has("steam_id")):
			user_info.steam_id = data_received["steam_id"]
		if(data_received.has("apple_id")):
			user_info.apple_id = data_received["apple_id"]
		if(data_received.has("google_play_id")):
			user_info.google_play_id = data_received["google_play_id"]
		if(data_received.has("facebook_id")):
			user_info.facebook_id = data_received["facebook_id"]
		if new_user_login:
			_new_login_succesful.emit()
			new_user_login = false
		# Send the appropriate signal back for login succes
		var signal_emitted = false
		for key in linking:
			if linking[key]:
				linking[key] = false
				signal_emitted = true
				emit_signal(key)
		if !signal_emitted:
			refresh_timer.wait_time = user_info.auth_token_expires_in - 15
			# Set the token refresh timer for 15 seconds before token expiration
			refresh_timer.start()
			aws_login_success.emit()
		
		
		
	else:
		print("JSON Parse Error: ", json.get_error_message(), " in ", json_string, " at line ", json.get_error_line())
		# Trigger callback from client side
		aws_login_error.emit(json.get_error_message())
		error_string = json.get_error_message()


# Logs in as a new guest user
func _login_as_new_guest():
	new_user_login = true
	# Perform a GET request to login as a new guest
	_make_auth_http_request(login_endpoint+"/login-as-guest")


# Logs in with existing user
func _login_as_guest():
	# Add the query parameters to the request
	var params = login_endpoint + "/login-as-guest?" + \
		"user_id" + "=" + user_info.user_id.uri_encode() + \
		"&guest_secret" + "=" + user_info.guest_secret.uri_encode()	
	# Perform a GET request to login as a new guest
	_make_auth_http_request(params)


# Refresh the access token with a refresh token
func login_with_refresh_token(refresh_token):
	print('refreshing token')
	var params = login_endpoint + "/refresh-access-token?" + \
		"refresh_token" + "=" + refresh_token.uri_encode()
	_make_auth_http_request(params)	
		

# Called to link an existing authenticated user to a Steam ID
func link_steam_id_to_current_user(steam_token, login_callback_steam):
	if user_info == null:
		aws_sdk_error.emit("No user info, can't link existing user to Steam ID")
		return
	linking["steam_link"] = true
	_login_with_steam(steam_token, user_info.auth_token, true)

	
# Called to create a new user with steam ID, or to login with existing user linked to Steam ID
func login_with_steam_token(steam_token):
	# Set the login callback
	linking["steam_login"] = true
	_login_with_steam(steam_token, null, false)

	
# Logs in with steam either linking existing user or as a steam only / new user
# Called internally by the different Steam login functions
func _login_with_steam(steam_token, auth_token, link_to_existing_user):
	# Add the steam token to request
	var params = login_endpoint+"/login-with-steam?" + \
		"steam_auth_token=" + steam_token.uri_encode()
	# If we're linking to existing user, add the relevant parameters
	if auth_token != null and link_to_existing_user == true:
		print("Linking Steam ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode() + \
			"&link_to_existing_user=Yes"
	_make_auth_http_request(params)	


# Called to link an existing authenticated user to a Apple ID
func link_apple_id_to_current_user(apple_auth_token):
	if user_info == null:
		aws_sdk_error.emit("No user info, can't link existing user to Apple ID")
		return
	linking["apple_link"] = true
	_login_with_apple_id(apple_auth_token, user_info.auth_token, true)

	
# Called to create a new user with Apple ID, or to login with existing user linked to AppleID
func login_with_apple_id_token(apple_auth_token):
	linking["apple_login"] = true
	_login_with_apple_id(apple_auth_token, null, false)


# Logs in with Apple ID either linking existing user or as a Apple ID only / new user
# Called internally by the different Apple ID login functions
func _login_with_apple_id(apple_auth_token, auth_token, link_to_existing_user):
	# Add the apple auth token to request
	var params = login_endpoint + "/login-with-apple-id?" + \
		"apple_auth_token=" + apple_auth_token.uri_encode()
	# If we're linking to existing user, add the relevant parameters
	if auth_token != null and link_to_existing_user == true:
		print("Linking Apple ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode() + \
			"&link_to_existing_user=Yes"
	# Perform a GET request to login as a new guest
	_make_auth_http_request(params)
	

# Called to link an existing authenticated user to a Google Play ID
func link_google_play_id_to_current_user(google_play_auth_token):
	if user_info == null:
		aws_sdk_error.emit("No user info, can't link existing user to Google Play ID")
		return
	linking["goog_link"] = true
	_login_with_google_play(google_play_auth_token, user_info.auth_token, true)

	
# Called to create a new user with Google Play ID, or to login with existing user linked to Google Play
func login_with_google_play_token(google_play_auth_token):
	linking["goog_login"] = true
	_login_with_google_play(google_play_auth_token, null, false)

	
# Logs in with Google Play ID either linking existing user or as a Google Play ID only / new user
# Called internally by the different Google Play ID login functions
func _login_with_google_play(google_play_auth_token, auth_token, link_to_existing_user):
	# Add the google play auth token to request
	var params = login_endpoint+"/login-with-google-play?" + \
		"google_play_auth_token" + "=" + google_play_auth_token.uri_encode()
	# If we're linking to existing user, add the relevant parameters
	if auth_token != null and link_to_existing_user == true:
		print("Linking Google Play ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode() + \
			"&link_to_existing_user=Yes"
	_make_auth_http_request(params)


# Called to link an existing authenticated user to a Facebook ID
func link_facebook_id_to_current_user(facebook_access_token, facebook_user_id):
	if(user_info == null):
		aws_sdk_error.emit("No user info, can't link existing user to Facebook ID")
		return
	linking["fb_link"] = true
	_login_with_facebook(facebook_access_token, facebook_user_id, user_info.auth_token, true)

	
# Called to create a new user with Facebook ID, or to login with existing user linked to Facebook
func login_with_facebook_access_token(facebook_access_token, facebook_user_id):
	linking["fb_login"] = true
	_login_with_facebook(facebook_access_token, facebook_user_id, null, false)

	
# Logs in with Facebook ID either linking existing user or as a Facebook ID only / new user
# Called internally by the different Facebook ID login functions
func _login_with_facebook(facebook_access_token, facebook_user_id, auth_token, link_to_existing_user):
	# Add the Facebook auth token and user ID to request
	var params = login_endpoint+"/login-with-facebook?" + \
		"facebook_access_token" + "=" + facebook_access_token.uri_encode() + \
		"&facebook_user_id" + "=" + facebook_user_id.uri_encode()
	# If we're linking to existing user, add the relevant parameters
	if auth_token != null and link_to_existing_user == true:
		print("Linking Facebook ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode() + \
			"&link_to_existing_user=Yes"
	# Perform a GET request to login as a new guest
	_make_auth_http_request(params)
