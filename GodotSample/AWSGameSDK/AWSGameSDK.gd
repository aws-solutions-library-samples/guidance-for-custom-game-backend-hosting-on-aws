extends Node

# Class to manage user info
class UserInfo:
	var user_id = "";
	var guest_secret = "";
	var auth_token = "";
	var apple_id = "";
	var steam_id = "";
	var google_play_id = "";
	var facebook_id = "";
	var refresh_token = "";
	var auth_token_expires_in = "";
	var refresh_token_expires_in = "";
	
	func _to_string():
		print("user_id: " + user_id + "\nguest_secret: " + guest_secret + "\nauth_token: " + auth_token +
				"\napple_id: " + apple_id + "\nsteam_id: " + steam_id + "\ngoogle_play_id: " + google_play_id
				+ "\nfacebook_id: " + facebook_id
				+ "\nrefresh_token: " + refresh_token + "\nauth_token_expires_in: " + str(auth_token_expires_in)
				+ "\nrefresh_token_expires_in: " + str(refresh_token_expires_in))

var login_endpoint = null # Endpoint for custom identity component passed in Init
var login_error_callback = null # Callback passed in Init for all login errors
var login_callback = null # Callback for the latest login request

var user_info = null # User info for the logged in user

var unix_time_for_auth_token_expiration = null # Set when login successful to do refreshes automatically

# Called when the node enters the scene tree for the first time.
func _ready():
	pass

# Called every frame. 'delta' is the elapsed time since the previous frame.
func _process(delta):
	
	# If we have a expiration time for auth token, check if we need to refresh
	if(self.unix_time_for_auth_token_expiration != null):
		var seconds_difference = self.unix_time_for_auth_token_expiration - Time.get_unix_time_from_system()
		
		# if it's less than 5 seconds to to expiration, renew
		if(seconds_difference < 5):
			self.unix_time_for_auth_token_expiration = null
			print("Refresh the access token")
			self.login_with_refresh_token(self.user_info.refresh_token)

func init(login_endpoint, login_error_callback):
	
	self.login_endpoint = login_endpoint
	self.login_error_callback = login_error_callback
	
	print("AWS Game SDK initialized")

# Logs in as a new guest user
func login_as_new_guest_user(login_callback):
	# Set the login callback
	self.login_callback = login_callback
	
	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)

	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/login-as-guest")
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint")

# Logs in with existing user
func login_as_guest(user_id, guest_secret, login_callback):
	
	# Set the login callback
	self.login_callback = login_callback

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)
	
	# Add the query parameters to the request
	var params = "?"
	params += "user_id" + "=" + user_id.uri_encode()
	params += "&guest_secret" + "=" + guest_secret.uri_encode()	
	
	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/login-as-guest"+params)
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint")

# Refresh the access token with a refresh token
func login_with_refresh_token(refresh_token, login_callback = null):
	
	# Set the login callback
	if(login_callback != null):
		self.login_callback = login_callback

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)
	
	# Add the query parameters to the request
	var params = "?"
	params += "refresh_token" + "=" + refresh_token.uri_encode()
	
	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/refresh-access-token"+params)
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint")
		

# Called to link an existing authenticated user to a Steam ID
func link_steam_id_to_current_user(steam_token, login_callback_steam):
	
	# Set the login callback
	if(login_callback_steam != null):
		self.login_callback = login_callback_steam
		
	if(self.user_info == null):
		self.login_error_callback.call("No user info, can't link existing user to Steam ID")
		return
		
	self.login_with_steam(steam_token, self.user_info.auth_token, true)
	
# Called to create a new user with steam ID, or to login with existing user linked to Steam ID
func login_with_steam_token(steam_token, login_callback):
	
	# Set the login callback
	if(login_callback != null):
		self.login_callback = login_callback
		
	self.login_with_steam(steam_token, null, false)
	
# Logs in with steam either linking existing user or as a steam only / new user
# Called internally by the different Steam login functions
func login_with_steam(steam_token, auth_token, link_to_existing_user):

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)
	
	# Add the steam token to request
	var params = "?"
	params += "steam_auth_token" + "=" + steam_token.uri_encode()
	
	# If we're linking to existing user, add the relevant parameters
	if(auth_token != null and link_to_existing_user == true):
		
		print("Linking Steam ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode()
		params += "&link_to_existing_user=Yes"
	
	print(login_endpoint+"/login-with-steam"+params)
	
	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/login-with-steam"+params)
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint for login-with-steam")

# Called to link an existing authenticated user to a Apple ID
func link_apple_id_to_current_user(apple_auth_token, login_callback_apple):
	
	# Set the login callback
	if(login_callback_apple != null):
		self.login_callback = login_callback_apple
		
	if(self.user_info == null):
		self.login_error_callback.call("No user info, can't link existing user to Apple ID")
		return
		
	self.login_with_apple_id(apple_auth_token, self.user_info.auth_token, true)
	
# Called to create a new user with Apple ID, or to login with existing user linked to AppleID
func login_with_apple_id_token(apple_auth_token, login_callback):
	
	# Set the login callback
	if(login_callback != null):
		self.login_callback = login_callback
		
	self.login_with_apple_id(apple_auth_token, null, false)
	
# Logs in with Apple ID either linking existing user or as a Apple ID only / new user
# Called internally by the different Apple ID login functions
func login_with_apple_id(apple_auth_token, auth_token, link_to_existing_user):

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)
	
	# Add the apple auth token to request
	var params = "?"
	params += "apple_auth_token" + "=" + apple_auth_token.uri_encode()
	
	# If we're linking to existing user, add the relevant parameters
	if(auth_token != null and link_to_existing_user == true):
		
		print("Linking Apple ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode()
		params += "&link_to_existing_user=Yes"
	
	print(login_endpoint+"/login-with-apple-id"+params)
	
	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/login-with-apple-id"+params)
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint for login-with-apple-id")

# Called to link an existing authenticated user to a Google Play ID
func link_google_play_id_to_current_user(google_play_auth_token, login_callback_google):
	
	# Set the login callback
	if(login_callback_google != null):
		self.login_callback = login_callback_google
		
	if(self.user_info == null):
		self.login_error_callback.call("No user info, can't link existing user to Google Play ID")
		return
		
	self.login_with_google_play(google_play_auth_token, self.user_info.auth_token, true)
	
# Called to create a new user with Google Play ID, or to login with existing user linked to Google Play
func login_with_google_play_token(google_play_auth_token, login_callback):
	
	# Set the login callback
	if(login_callback != null):
		self.login_callback = login_callback
		
	self.login_with_google_play(google_play_auth_token, null, false)
	
# Logs in with Google Play ID either linking existing user or as a Google Play ID only / new user
# Called internally by the different Google Play ID login functions
func login_with_google_play(google_play_auth_token, auth_token, link_to_existing_user):

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)
	
	# Add the google play auth token to request
	var params = "?"
	params += "google_play_auth_token" + "=" + google_play_auth_token.uri_encode()
	
	# If we're linking to existing user, add the relevant parameters
	if(auth_token != null and link_to_existing_user == true):
		
		print("Linking Google Play ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode()
		params += "&link_to_existing_user=Yes"
	
	print(login_endpoint+"/login-with-google-play"+params)
	
	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/login-with-google-play"+params)
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint for login-with-google-play")

# Called to link an existing authenticated user to a Facebook ID
func link_facebook_id_to_current_user(facebook_access_token, facebook_user_id, login_callback_facebook):
	
	# Set the login callback
	if(login_callback_facebook != null):
		self.login_callback = login_callback_facebook
		
	if(self.user_info == null):
		self.login_error_callback.call("No user info, can't link existing user to Facebook ID")
		return
		
	self.login_with_facebook(facebook_access_token, facebook_user_id, self.user_info.auth_token, true)
	
# Called to create a new user with Facebook ID, or to login with existing user linked to Facebook
func login_with_facebook_access_token(facebook_access_token, facebook_user_id, login_callback):
	
	# Set the login callback
	if(login_callback != null):
		self.login_callback = login_callback
		
	self.login_with_facebook(facebook_access_token, facebook_user_id, null, false)
	
# Logs in with Facebook ID either linking existing user or as a Facebook ID only / new user
# Called internally by the different Facebook ID login functions
func login_with_facebook(facebook_access_token, facebook_user_id, auth_token, link_to_existing_user):

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(self.sdk_login_callback)
	
	# Add the Facebook auth token and user ID to request
	var params = "?"
	params += "facebook_access_token" + "=" + facebook_access_token.uri_encode()
	params += "&facebook_user_id" + "=" + facebook_user_id.uri_encode()
	
	# If we're linking to existing user, add the relevant parameters
	if(auth_token != null and link_to_existing_user == true):
		
		print("Linking Facebook ID to existing user")
		params += "&auth_token" + "=" + auth_token.uri_encode()
		params += "&link_to_existing_user=Yes"
	
	print(login_endpoint+"/login-with-facebook"+params)
	
	# Perform a GET request to login as a new guest
	var error = http_request.request(login_endpoint+"/login-with-facebook"+params)
	
	# In case of error, trigger the error callback
	if error != OK:
		self.login_error_callback.call("Error making request to login endpoint for login-with-facebook")



# callback for login or refresh requests
func sdk_login_callback(result, response_code, headers, body):
	var json_string = body.get_string_from_utf8() # Retrieve data
	var json = JSON.new()
	var error = json.parse(json_string)
	
	# trigger error if we didn't get a proper response code
	if(response_code >= 400):
		self.login_error_callback.call(json_string)
		return
	
	# Check we got no error
	if error == OK:
		var data_received = json.data
		# Check that we got a user_id (valid response)
		if(!data_received.has("user_id")):
			self.login_error_callback.call(json_string)
			return
		
		# We got valid response, let's parse values to UserInfo object
		#print(data_received)
		if(self.user_info == null):
			self.user_info = UserInfo.new()
		self.user_info.user_id = data_received["user_id"]
		if(data_received.has("guest_secret")):
			self.user_info.guest_secret = data_received["guest_secret"]
		if(data_received.has("auth_token")):
			self.user_info.auth_token = data_received["auth_token"]
		if(data_received.has("refresh_token")):
			self.user_info.refresh_token = data_received["refresh_token"]
		if(data_received.has("auth_token_expires_in")):
			self.user_info.auth_token_expires_in = data_received["auth_token_expires_in"]
		if(data_received.has("refresh_token_expires_in")):
			self.user_info.refresh_token_expires_in = data_received["refresh_token_expires_in"]
		if(data_received.has("steam_id")):
			self.user_info.steam_id = data_received["steam_id"]
		if(data_received.has("apple_id")):
			self.user_info.apple_id = data_received["apple_id"]
		if(data_received.has("google_play_id")):
			self.user_info.google_play_id = data_received["google_play_id"]
		if(data_received.has("facebook_id")):
			self.user_info.facebook_id = data_received["facebook_id"]
		
		# Get the current UNIX time, and add the seconds for auth_token expiration
		self.unix_time_for_auth_token_expiration = Time.get_unix_time_from_system() + self.user_info.auth_token_expires_in
		#print(self.user_info)
		
		# Send the login info back to original requester
		if(self.login_callback != null):
			self.login_callback.call(self.user_info)
		
	else:
		print("JSON Parse Error: ", json.get_error_message(), " in ", json_string, " at line ", json.get_error_line())
		# Trigger callback from client side
		self.login_error_callback.call(json.get_error_message())
		
# Function to make an authenticated request to a backend API
# Called by your custom code to access backend functionality
func backend_get_request(url, resource, query_parameters, callback):
	
	if(self.user_info == null):
		callback.call("Error: no user info set yet, login first")
		return
	
	if(self.user_info.auth_token == ""):
		callback.call("No auth token set yet, login first")
		return
	
	# Add the query parameters to the request
	if(query_parameters != null):
		resource += "?"
		for key in query_parameters:
			resource += "&" + key + "=" + query_parameters[key].uri_encode() 

	# Create an HTTP request node and connect its completion signal.
	var http_request = HTTPRequest.new()
	add_child(http_request)
	http_request.request_completed.connect(callback)
	
	print(url+resource)
	# Perform a GET request to login as a new guest
	var error = http_request.request(url+resource, ["Authorization: " + self.user_info.auth_token], HTTPClient.METHOD_GET)
	
	if error != OK:
		callback.call("Error with HTTP request")
			
