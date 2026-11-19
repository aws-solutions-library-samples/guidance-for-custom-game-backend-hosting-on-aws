class_name UserInfo

# Class to manage user info
var user_id = "";
var guest_secret = "";
var auth_token = "";
var apple_id = "";
var steam_id = "";
var google_play_id = "";
var facebook_id = "";
var refresh_token = "";
var auth_token_expires_in = 0;
var refresh_token_expires_in = 0;
	
func to_string():
	return("user_id: " + user_id + "\nguest_secret: " + guest_secret + "\nauth_token: " + auth_token +
			"\napple_id: " + apple_id + "\nsteam_id: " + steam_id + "\ngoogle_play_id: " + google_play_id
			+ "\nfacebook_id: " + facebook_id
			+ "\nrefresh_token: " + refresh_token + "\nauth_token_expires_in: " + str(auth_token_expires_in)
			+ "\nrefresh_token_expires_in: " + str(refresh_token_expires_in))
