# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
extends Node
class_name AWSGameSDKBackend

signal aws_backend_request_successful
signal aws_sdk_error

@export var backend_endpoint: String = "" #Endpoint for backend operations
@export var gamelift_backend_endpoint: String = "" #Endpoint for GameLift Backend
@export var get_player_data_uri: String = "/get-player-data"
@export var set_player_data_uri: String = "/set-player-data"
@export var post_player_data_uri: String = "/post-to-uri"
@export var gamelift_request_match_uri: String = "/request-matchmaking"
@export var gamelift_match_status_uri: String = "/get-match-status"

var http_request: HTTPRequest
var error_string: String
#data cannot be a forced type at this time, as some data is returned as text and some as json
#you will need to evaluate the data returned for typing
var data_received


func get_response_data():
	return data_received

	
# Functions to make an authenticated GET request to a backend API
# Called by your custom code to access backend functionality
func backend_set_request(auth_token: String, query_parameters: Dictionary = {}):
	_backend_get_request(set_player_data_uri, auth_token, query_parameters)
	return
	
	
func backend_get_request(auth_token:String, query_parameters: Dictionary = {}):
	_backend_get_request(get_player_data_uri, auth_token, query_parameters)
	return


func backend_gamelift_request(auth_token: String, query_parameters: Dictionary = {}):
	pass



func _backend_get_request(resource: String, auth_token: String, query_parameters: Dictionary = {}):
	var params: String = ""
	if(auth_token == ""):
		aws_sdk_error.emit("No auth token set yet, login first")
		return
	# Add the query parameters to the request
	params += backend_endpoint + resource
	if query_parameters != null and query_parameters != {}:
		params += "?"
		for key in query_parameters:
			params += key + "=" + query_parameters[key].uri_encode() + "&"
	# Perform a GET request to login as a new guest
	_make_backend_http_request(params, HTTPClient.Method.METHOD_GET, auth_token)


func gamelift_backend_post_request(auth_token, request_body: Dictionary = {}):
	if auth_token == "":
		aws_sdk_error.emit("No auth token set yet, login first")
		return
	# Create an HTTP request node and connect its completion signal.
	var params = gamelift_backend_endpoint + gamelift_request_match_uri
	# Perform a GET request to login as a new guest
	_make_backend_http_request(params, HTTPClient.Method.METHOD_POST, auth_token, request_body)


func gamelift_backend_get_request(auth_token, request_body: Dictionary = {}):
	if auth_token == "":
		aws_sdk_error.emit("No auth token set yet, login first")
		return
	# Create an HTTP request node and connect its completion signal.
	var params = gamelift_backend_endpoint + gamelift_request_match_uri
	# Perform a GET request to login as a new guest
	_make_backend_http_request(params, HTTPClient.Method.METHOD_GET, auth_token, request_body)

# Function to make an authenticated POST request to a backend API
# Called by your custom code to access backend functionality
func backend_post_request(auth_token, request_body: Dictionary = {}):
	if auth_token == "":
		aws_sdk_error.emit("No auth token set yet, login first")
		return
	# Create an HTTP request node and connect its completion signal.
	var params = backend_endpoint + post_player_data_uri
	# Perform a GET request to login as a new guest
	_make_backend_http_request(params, HTTPClient.Method.METHOD_POST, auth_token, request_body)


func _make_backend_http_request(url, method: HTTPClient.Method, auth_token: String, request_body: Dictionary = {}):
	var error
	#clear out prior data 
	data_received = {}
	http_request = HTTPRequest.new()
	http_request.request_completed.connect(_backend_reqeust_completed)
	add_child(http_request)
	var headers = ["Authorization: " + auth_token]
	if method == HTTPClient.Method.METHOD_POST:
		error = http_request.request(url, headers, method, str(request_body))
	elif method == HTTPClient.Method.METHOD_GET:
		error = http_request.request(url, headers, method)
	else:
		#unsupported method at this time
		aws_sdk_error.emit("Unsupported HTTP verb in request")
	if error != OK:
		print(error)
		aws_sdk_error.emit("Error making backend request")			
	

func _backend_reqeust_completed(result, response_code, headers, body):
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
		data_received = json.data
	aws_backend_request_successful.emit()
	pass
