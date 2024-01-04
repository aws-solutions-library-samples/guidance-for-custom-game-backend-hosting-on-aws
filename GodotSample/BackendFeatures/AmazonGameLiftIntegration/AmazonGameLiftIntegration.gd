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

var latency_data # The JSON latency data for requesting matchmaking
	
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
	
# Measures TCP latency to an endpoint with 3 requests (1 for establishing HTTPS, 2 for average TCP)
func measure_tcp_latency(endpoint):
	
	# We'll use HTTPClient to reuse the connection
	var http_client = HTTPClient.new()
	http_client.connect_to_host(endpoint)
	while http_client.get_status() == HTTPClient.STATUS_CONNECTING or http_client.get_status() == HTTPClient.STATUS_RESOLVING:
		http_client.poll()
		if not OS.has_feature("web"):
			OS.delay_msec(1)
		else:
			await get_tree().process_frame
			
	# Measure the two requests
	var start_time = Time.get_ticks_msec()
	
	var headers = [
		"User-Agent: Pirulo/1.0 (Godot)",
		"Accept: */*"
	]
	var err = http_client.request(HTTPClient.METHOD_GET, "/", headers) # Request a page from the site (this one was chunked..)
	assert(err == OK) # Make sure all is OK.

	while http_client.get_status() == HTTPClient.STATUS_REQUESTING:
		# Keep polling for as long as the request is being processed.
		http_client.poll()
		if OS.has_feature("web"):
			# Synchronous HTTP requests are not supported on the web,
			# so wait for the next main loop iteration.
			await get_tree().process_frame
		else:
			OS.delay_msec(1)
			
	var rb = PackedByteArray() # Array that will hold the data.

	while http_client.get_status() == HTTPClient.STATUS_BODY:
		# While there is body left to be read
		http_client.poll()
		# Get a chunk.
		var chunk = http_client.read_response_body_chunk()
		if chunk.size() == 0:
			if not OS.has_feature("web"):
				# Got nothing, wait for buffers to fill a bit.
				OS.delay_msec(1)
			else:
				await get_tree().process_frame
		else:
			rb = rb + chunk # Append to read buffer.
	
	err = http_client.request(HTTPClient.METHOD_GET, "/", headers) # Request a page from the site (this one was chunked..)
	assert(err == OK) # Make sure all is OK.

	while http_client.get_status() == HTTPClient.STATUS_REQUESTING:
		# Keep polling for as long as the request is being processed.
		http_client.poll()
		if OS.has_feature("web"):
			await get_tree().process_frame
		else:
			OS.delay_msec(1)
		
	while http_client.get_status() == HTTPClient.STATUS_BODY:
		# While there is body left to be read
		http_client.poll()
		# Get a chunk.
		var chunk = http_client.read_response_body_chunk()
		if chunk.size() == 0:
			if not OS.has_feature("web"):
				# Got nothing, wait for buffers to fill a bit.
				OS.delay_msec(1)
			else:
				await get_tree().process_frame
		else:
			rb = rb + chunk # Append to read buffer.
			
	var end_time = Time.get_ticks_msec()
	var total_time = (int)((end_time - start_time) / 2.0)
	
	return total_time
	
# Measures the TCP latencies to the 3 default regions and returns the JSON string 
func measure_latencies():
	
	var region1 = "us-east-1"
	var region2 = "us-west-2"
	var region3 = "eu-west-1"
	
	# Make three HTTP requests to each endpoint and measure the latter 2 after handshake
	var region1latency = await self.measure_tcp_latency("https://dynamodb." + region1 + ".amazonaws.com")
	var region2latency = await self.measure_tcp_latency("https://dynamodb." + region2 + ".amazonaws.com")
	var region3latency = await self.measure_tcp_latency("https://dynamodb." + region3 + ".amazonaws.com")

	var latencydata = { "latencyInMs": { "us-east-1": region1latency, "us-west-2": region2latency, "eu-west-1": region3latency}}
	
	return JSON.stringify(latencydata)
#func _http_request_completed(result, response_code, headers, body):
	
# Called when the node enters the scene tree for the first time.
func _ready():
	
	# Measure latencies to regional endpoints first
	self.latency_data = await self.measure_latencies()
	
	print("Got latency data: " + self.latency_data)
	
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
	self.aws_game_sdk.backend_post_request(self.gamelift_integration_backend_endpoint, "/request-matchmaking",
											self.latency_data, self.matchmaking_request_callback)
	
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
		self.connect_to_server(dict_response.data["IpAddress"], dict_response.data["Port"], dict_response.data["PlayerSessionId"])
	else:
		print("Matchmaking failed or timed out.")
	
	self.total_tries += 1

func connect_to_server(host, port, player_session_id):
	
	var _status: int = 0
	var _stream: StreamPeerTCP = StreamPeerTCP.new()
	
	print("Connecting to: " + host + ":" + port)
	# Reset status so we can tell if it changes to error again.
	_status = _stream.STATUS_NONE
	if _stream.connect_to_host(host, int(port)) != OK:
		print("Error connecting to host.")
		return
	
	# Wait for the stream to connect
	while _status == _stream.STATUS_CONNECTING or _status == _stream.STATUS_NONE:
		_stream.poll()
		_status = _stream.get_status()
		print("Status: ", _status)
		print("Waiting for connection...")
		await get_tree().create_timer(0.1).timeout
	
	# If we got an error, abort
	if _status == _stream.STATUS_ERROR:
		print("Couldn't connect to server.")
		return
		
	# Send our player session ID
	print("Sending player session ID: " + player_session_id)
	_stream.put_data(player_session_id.to_ascii_buffer())
	
	# Receive the response from server
	while(true):
		_status = _stream.get_status()
		match _status:
			_stream.STATUS_NONE:
				print("Disconnected from host.")
				return
			_stream.STATUS_ERROR:
				print("Error with socket stream.")
				return

		if _status == _stream.STATUS_CONNECTED:
			var available_bytes: int = _stream.get_available_bytes()
			if available_bytes > 0:
				print("available bytes: ", available_bytes)
				var response = _stream.get_string(available_bytes)
				print("Got response: " + response)
				return
					
		# Wait a frame
		await get_tree().process_frame
		
# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
