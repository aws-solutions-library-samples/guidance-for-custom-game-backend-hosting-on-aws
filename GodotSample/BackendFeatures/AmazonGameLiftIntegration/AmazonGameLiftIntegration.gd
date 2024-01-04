# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

extends Node

# TODO: Add the login endpoint here
const login_endpoint = "https://YOUR_ENDPOINT/prod/"
# TODO: Add your Amazon GameLift backend component endpoint here
const gamelift_integration_backend_endpoint = "https://YOUR_ENDPOINT/prod"

var aws_game_sdk

var ticket_id
var total_tries = 0 # The amount of tries to get match status

func save_login_data(user_id, guest_secret):
	var file = FileAccess.open("user://save_game.dat", FileAccess.WRITE)
	file.store_pascal_string(user_id)
	file.store_pascal_string(guest_secret)
	file = null
	
func load_login_data():
	var file = FileAccess.open("user://save_game2.dat", FileAccess.READ)
	if(file == null or file.get_length() == 0):
		return null;
	
	var user_id = file.get_pascal_string()
	var guest_secret = file.get_pascal_string()
	return [user_id, guest_secret]

# Called when the node enters the scene tree for the first time.
func _ready():
	
	# Get the SDK and Init
	self.aws_game_sdk = get_node("/root/AwsGameSdk")
	self.aws_game_sdk.init(self.login_endpoint, self.on_login_error)
	
	# Try to load existing user info
	var stored_user_info = self.load_login_data()
	
	# If we have stored user info, login with existing user
	if(stored_user_info != null):
		print("Logging in with existing user: " + stored_user_info[0])
		self.aws_game_sdk.login_as_guest(stored_user_info[0], stored_user_info[1], self.login_callback)
	# Else we login as new user
	else:
		print("Logging in as new user")
		self.aws_game_sdk.login_as_new_guest_user(self.login_callback)

# Called on any login or token refresh failures
func on_login_error(message):
	print("Login error: " + message)

# Receives a UserInfo object after successful login
func login_callback(user_info):
	print("Received login info.")
	print(user_info)
	
	# Store the login info for future logins
	self.save_login_data(user_info.user_id, user_info.guest_secret)
	
	# Start matchmaking
	self.aws_game_sdk.backend_post_request(self.gamelift_integration_backend_endpoint, "/request-matchmaking", "{ \"latencyInMs\": { \"us-east-1\" : 10, \"us-west-2\" : 20, \"eu-west-1\" : 30 }}", self.matchmaking_request_callback)
	
# We need to use the exact format of the callback required for HTTPRequest
func matchmaking_request_callback(result, response_code, headers, body):
	
	var string_response = body.get_string_from_utf8()
	
	if(response_code >= 400):
		print("Error code " + str(response_code) + " Message: " + string_response)
		return
		
	print("Matchmaking request response: " + string_response)
	
	# Extract the ticket ID from the response
	var dict_response = JSON.new()
	var error = dict_response.parse(string_response)
	
	if(error != OK):
		print("Couldn't parse ticket ID from response")
	else:
		self.ticket_id = dict_response.data["TicketId"]
		print("Ticket id: " + self.ticket_id)
		# Call the get match status
		self.aws_game_sdk.backend_get_request(self.gamelift_integration_backend_endpoint, "/get-match-status", { "ticketId" : self.ticket_id}, self.get_match_status_callback)

# We need to use the exact format of the callback required for HTTPRequest
func get_match_status_callback(result, response_code, headers, body):
	
	var string_response = body.get_string_from_utf8()
		
	print("Match status response: " + string_response)
	
	# Extract the response to dictionary
	var dict_response = JSON.new()
	var error = dict_response.parse(string_response)
	
	var ticket_status = null
	# Get the status of matchmaking if we got a valid response
	if error == OK and typeof(dict_response.data) == TYPE_DICTIONARY and dict_response.data.has("MatchmakingStatus"):
		ticket_status = dict_response.data["MatchmakingStatus"]
		print("Got ticket status: " + ticket_status)
	# Get match status again if we're not in the end state yet
	if ticket_status == null or ticket_status == "MatchmakingQueued" or ticket_status == "MatchmakingSearching" or ticket_status == "PotentialMatchCreated":
			print("Not in end state yet, get match status again after 1.5s")
			# Only try a total of 15 times
			if self.total_tries > 15:
				print("Couldn't get a valid response from matchmaking")
			else:
				await get_tree().create_timer(1.5).timeout
				self.aws_game_sdk.backend_get_request(self.gamelift_integration_backend_endpoint, "/get-match-status", { "ticketId" : self.ticket_id}, self.get_match_status_callback)
	elif ticket_status == "MatchmakingSucceeded":
		print("Matchmaking done, connect to server...")
		# TODO: Connect
	else:
		print("Matchmaking failed or timed out.")
	
	self.total_tries += 1
	

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
