from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.models import EmailAddress

class MySocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        """
        Automatically connect social accounts to existing users with matching email.
        This allows pre-populated users to login seamlessly via Google.
        """
        # If the social account is already connected, just update info
        if sociallogin.is_existing:
            user = sociallogin.user
            extra_data = sociallogin.account.extra_data
            
            changed = False
            
            # Update name
            name = extra_data.get('name')
            if name and user.name != name:
                user.name = name
                changed = True
            
            # Update avatar
            picture = extra_data.get('picture')
            if picture and hasattr(user, 'avatar') and user.avatar != picture:
                user.avatar = picture
                changed = True
                
            if changed:
                user.save()
            return
        
        # If not connected yet, check if a user exists with this email
        if sociallogin.email_addresses:
            email = sociallogin.email_addresses[0].email.lower()
            from users.models import User
            
            try:
                # Try to find existing user by email
                user = User.objects.get(email__iexact=email)
                
                # Connect the social account to this existing user
                sociallogin.connect(request, user)
                
                # Update user info from Google
                extra_data = sociallogin.account.extra_data
                changed = False
                
                name = extra_data.get('name')
                if name and user.name != name:
                    user.name = name
                    changed = True
                
                picture = extra_data.get('picture')
                if picture and hasattr(user, 'avatar') and user.avatar != picture:
                    user.avatar = picture
                    changed = True
                
                if changed:
                    user.save()
                    
            except User.DoesNotExist:
                # No existing user, let the normal signup flow continue
                pass
    
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        extra_data = sociallogin.account.extra_data
        
        # Name
        name = extra_data.get('name') or data.get('name')
        if name:
            user.name = name
        
        # Avatar
        picture = extra_data.get('picture')
        if picture and hasattr(user, 'avatar'):
            user.avatar = picture
            
        return user
