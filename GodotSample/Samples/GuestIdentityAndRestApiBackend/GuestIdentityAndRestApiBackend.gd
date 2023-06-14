extends Node

# TODO: Add the login endpoint here
const login_endpoint = "https://YOURENDPOINTHERE/prod/"
# TODO: Add your backend component endpoint here
const backend_endpoint = "https://YOURENDPOINTHERE/prod"

var aws_game_sdk

func save_login_data(user_id, guest_secret):
	var file = FileAccess.open("user://save_game.dat", FileAccess.WRITE)
	file.store_pascal_string(user_id)
	file.store_pascal_string(guest_secret)
	file = null
	
func load_login_data():
	var file = FileAccess.open("user://save_game.dat", FileAccess.READ)
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
	
	# Try setting player data
	self.aws_game_sdk.backend_get_request(self.backend_endpoint, "/set-player-data", {"player_name" : "John Doe"}, self.set_player_data_callback)
	
# We need to use the exact format of the callback required for HTTPRequest
func set_player_data_callback(result, response_code, headers, body):
	
	var string_response = body.get_string_from_utf8()
	
	if(response_code >= 400):
		print("Error code " + str(response_code) + " Message: " + string_response)
		return
		
	print("Successfully set player data: " + string_response)
	
	# Test getting the same date next
	self.aws_game_sdk.backend_get_request(self.backend_endpoint, "/get-player-data", null, self.get_player_data_callback)

# We need to use the exact format of the callback required for HTTPRequest
func get_player_data_callback(result, response_code, headers, body):
	
	var string_response = body.get_string_from_utf8()
	
	if(response_code >= 400):
		print("Error code " + str(response_code) + " Message: " + string_response)
		return
	
	print("Success received player data: " + string_response)
# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	pass
