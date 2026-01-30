import discord
from discord.ext import commands
from discord import app_commands
import requests
from datetime import datetime, timezone
import matplotlib.pyplot as plt
import io
import os

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Toggle this: True for global sync (test), False for guild-only (production)
USE_GLOBAL_SYNC = True  # Set to False after testing

# Your Discord user ID for restricted commands
AUTHORIZED_USER_ID = 1459581008025227518

# --- Roblox API Functions ---

def get_group_members_with_ranks(group_id):
    members = {}
    url = f"https://groups.roblox.com/v1/groups/{group_id}/users"
    params = {"sortOrder": "Asc", "limit": 100}
    try:
        while True:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return f"Error fetching members for group {group_id}: {response.status_code}"
            data = response.json()
            for user_data in data.get("data", []):
                username = user_data["user"]["username"]
                role = user_data.get("role", {})
                rank_name = role.get("name", "Unknown")
                rank_num = role.get("rank", "Unknown")
                members[username] = {"rank_name": rank_name, "rank_num": rank_num}
            next_page_cursor = data.get("nextPageCursor")
            if not next_page_cursor:
                break
            params["cursor"] = next_page_cursor
    except Exception as e:
        return f"Network or other error fetching group {group_id}: {e}"
    return members

def get_user_profile(user_id):
    try:
        user_url = f"https://users.roblox.com/v1/users/{user_id}"
        user_response = requests.get(user_url, timeout=10)
        if user_response.status_code != 200:
            return f"Error fetching user info: {user_response.status_code}"
        user_data = user_response.json()
        username = user_data.get("name", "Unknown")
        display_name = user_data.get("displayName", "Unknown")
        description = user_data.get("description", "No description")
        created = user_data.get("created", "Unknown")
        join_date = datetime.fromisoformat(created[:-1]).replace(tzinfo=timezone.utc) if created != "Unknown" else None
        account_age_days = (datetime.now(timezone.utc) - join_date).days if join_date else "Unknown"
        friends_count = requests.get(f"https://friends.roblox.com/v1/users/{user_id}/friends/count", timeout=10).json().get("count", 0)
        followers_count = requests.get(f"https://friends.roblox.com/v1/users/{user_id}/followers/count", timeout=10).json().get("count", 0)
        following_count = requests.get(f"https://friends.roblox.com/v1/users/{user_id}/followings/count", timeout=10).json().get("count", 0)
        badges = []
        badges_url = f"https://badges.roblox.com/v1/users/{user_id}/badges"
        params = {"limit": 100}
        while True:
            badges_response = requests.get(badges_url, params=params, timeout=10)
            if badges_response.status_code != 200:
                break
            badges_data = badges_response.json()
            badges.extend([badge["name"] for badge in badges_data.get("data", [])])
            next_cursor = badges_data.get("nextPageCursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor
        total_badges = len(badges)
        risk_score = 0
        risk_factors = []
        if account_age_days != "Unknown" and account_age_days < 365:
            risk_score += 2
            risk_factors.append("Recent account (<1 year)")
        if friends_count < 10:
            risk_score += 1
            risk_factors.append("Low friends (<10)")
        if followers_count < 50:
            risk_score += 1
            risk_factors.append("Low followers (<50)")
        if total_badges < 5:
            risk_score += 1
            risk_factors.append("Few badges (<5)")
        risk_level = "Low" if risk_score <= 1 else "Medium" if risk_score <= 3 else "High"
        return {
            "username": username,
            "display_name": display_name,
            "description": description,
            "join_date": created,
            "account_age_days": account_age_days,
            "friends_count": friends_count,
            "followers_count": followers_count,
            "following_count": following_count,
            "total_badges": total_badges,
            "badges_list": badges[:20],
            "risk_level": risk_level,
            "risk_factors": risk_factors
        }
    except Exception as e:
        print(f"Profile fetch error for user {user_id}: {e}")  # Log for debugging
        return "Unable to retrieve profile data. Please check the User ID."

def get_user_badges_with_dates(user_id):
    badges = []
    url = f"https://badges.roblox.com/v1/users/{user_id}/badges"
    params = {"limit": 100}
    try:
        while True:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return f"Error fetching badges: {response.status_code}"
            data = response.json()
            for badge in data.get("data", []):
                awarded_date = badge.get("awardedDate")
                if awarded_date:
                    date = datetime.fromisoformat(awarded_date[:-1]).replace(tzinfo=timezone.utc)
                    badges.append(date)
            next_cursor = data.get("nextPageCursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor
    except Exception as e:
        print(f"Badges fetch error for user {user_id}: {e}")  # Log for debugging
        return "Unable to retrieve badge data. Please check the User ID."
    return sorted(badges)  # Sort by date

def get_user_groups(user_id):
    groups = []
    url = f"https://groups.roblox.com/v1/users/{user_id}/groups"
    params = {"limit": 100}
    try:
        while True:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code != 200:
                return f"Error fetching groups: {response.status_code}"
            data = response.json()
            for group in data.get("data", []):
                groups.append({
                    "name": group["group"]["name"],
                    "rank": group["role"]["name"],
                    "rank_num": group["role"]["rank"]
                })
            next_cursor = data.get("nextPageCursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor
    except Exception as e:
        return f"Network error fetching groups: {e}"
    return groups

def compare_users(user_id_1, user_id_2):
    try:
        friends_1_response = requests.get(f"https://friends.roblox.com/v1/users/{user_id_1}/friends", timeout=10).json().get("data", [])
        friends_2_response = requests.get(f"https://friends.roblox.com/v1/users/{user_id_2}/friends", timeout=10).json().get("data", [])
        friends_1 = {f.get("name") or f.get("username", "Unknown") for f in friends_1_response if f.get("name") or f.get("username")}
        friends_2 = {f.get("name") or f.get("username", "Unknown") for f in friends_2_response if f.get("name") or f.get("username")}
        common_friends = friends_1 & friends_2
        following_1_response = requests.get(f"https://friends.roblox.com/v1/users/{user_id_1}/followings", timeout=10).json().get("data", [])
        following_2_response = requests.get(f"https://friends.roblox.com/v1/users/{user_id_2}/followings", timeout=10).json().get("data", [])
        following_1 = {f.get("name") or f.get("username", "Unknown") for f in following_1_response if f.get("name") or f.get("username")}
        following_2 = {f.get("name") or f.get("username", "Unknown") for f in following_2_response if f.get("name") or f.get("username")}
        common_followers = following_1 & following_2
        groups_1 = get_user_groups(user_id_1)
        groups_2 = get_user_groups(user_id_2)
        if isinstance(groups_1, str) or isinstance(groups_2, str):
            return "Unable to retrieve group data for comparison. Please check the User IDs."
        group_names_1 = {g["name"] for g in groups_1}
        group_names_2 = {g["name"] for g in groups_2}
        common_groups = group_names_1 & group_names_2
        commonality_score = len(common_friends) + len(common_followers) + len(common_groups)
        threat_level = "Low" if commonality_score < 5 else "Medium" if commonality_score <= 15 else "High"
        return {
            "common_friends": list(common_friends),
            "common_followers": list(common_followers),
            "common_groups": list(common_groups),
            "threat_level": threat_level
        }
    except Exception as e:
        print(f"Comparison error for users {user_id_1} and {user_id_2}: {e}")  # Log for debugging
        return "Unable to perform comparison. Please check the User IDs."

# --- Discord Commands ---

class GroupCheckView(discord.ui.View):
    def __init__(self, pages, current_page=0):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = current_page
        self.update_buttons()

    def update_buttons(self):
        self.children[0].disabled = self.current_page == 0  # Previous
        self.children[1].disabled = self.current_page == len(self.pages) - 1  # Next

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.grey)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.grey)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            self.update_buttons()
            await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

@bot.tree.command(name="group_check", description="Check users in both Roblox groups and show their ranks.")
@app_commands.describe(group_id_1="Group - 1", group_id_2="Group - 2")
async def group_check(interaction: discord.Interaction, group_id_1: int, group_id_2: int):
    await interaction.response.defer()
    members_1 = get_group_members_with_ranks(group_id_1)
    if isinstance(members_1, str):
        embed = discord.Embed(title="Error", description="Unable to retrieve group data. Please check the Group IDs.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    members_2 = get_group_members_with_ranks(group_id_2)
    if isinstance(members_2, str):
        embed = discord.Embed(title="Error", description="Unable to retrieve group data. Please check the Group IDs.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    intersection = set(members_1.keys()) & set(members_2.keys())
    if not intersection:
        embed = discord.Embed(title="No Common Users", description="No users are in both groups.", color=0x808080)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    intersection_list = list(intersection)
    pages = []
    per_page = 5
    for i in range(0, len(intersection_list), per_page):
        page_users = intersection_list[i:i + per_page]
        description = ""
        for username in page_users:
            rank_1 = members_1[username]
            rank_2 = members_2[username]
            description += f"**{username}**\n- Rank in {group_id_1}: {rank_1['rank_name']} ({rank_1['rank_num']})\n- Rank in {group_id_2}: {rank_2['rank_name']} ({rank_2['rank_num']})\n\n"
        embed = discord.Embed(title=f"Common Users in Groups {group_id_1} and {group_id_2}", description=description, color=0x808080)
        embed.add_field(name="Total Common Users", value=str(len(intersection_list)), inline=True)
        embed.set_footer(text="Information extracted from ISB database.")
        pages.append(embed)
    view = GroupCheckView(pages)
    await interaction.followup.send(embed=pages[0], view=view)

@bot.tree.command(name="profile_analysis", description="Advanced Roblox profile check for ALT risk analysis.")
@app_commands.describe(user_id="Roblox User ID")
async def profile_analysis(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    profile = get_user_profile(user_id)
    if isinstance(profile, str):
        embed = discord.Embed(title="Error", description=profile, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    threat_level = profile["risk_level"]
    color = 0x00FF00 if threat_level == "Low" else 0xFFA500 if threat_level == "Medium" else 0xFF0000
    embed = discord.Embed(title=f"Profile Analysis: {profile['username']}", color=color)
    embed.add_field(name="Display Name", value=profile['display_name'], inline=True)
    embed.add_field(name="Description", value=profile['description'][:200], inline=False)
    embed.add_field(name="Join Date", value=profile['join_date'], inline=True)
    embed.add_field(name="Account Age", value=f"{profile['account_age_days']} days", inline=True)
    embed.add_field(name="Friends", value=str(profile['friends_count']), inline=True)
    embed.add_field(name="Followers", value=str(profile['followers_count']), inline=True)
    embed.add_field(name="Following", value=str(profile['following_count']), inline=True)
    embed.add_field(name="Total Badges", value=str(profile['total_badges']), inline=True)
    embed.add_field(name="Badges", value=', '.join(profile['badges_list']) or 'None', inline=False)
    embed.add_field(name="Threat Level", value=threat_level, inline=True)
    embed.add_field(name="Risk Factors", value=', '.join(profile['risk_factors']) if profile['risk_factors'] else 'None', inline=False)
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="compare_users", description="Compare common friends, followers, and groups between two Roblox users.")
@app_commands.describe(user_id_1="First Roblox User ID", user_id_2="Second Roblox User ID")
async def compare_users_command(interaction: discord.Interaction, user_id_1: int, user_id_2: int):
    await interaction.response.defer()
    comparison = compare_users(user_id_1, user_id_2)
    if isinstance(comparison, str):
        embed = discord.Embed(title="Error", description=comparison, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    threat_level = comparison["threat_level"]
    color = 0x00FF00 if threat_level == "Low" else 0xFFA500 if threat_level == "Medium" else 0xFF0000
    embed = discord.Embed(title=f"User Comparison: {user_id_1} vs {user_id_2}", color=color)
    embed.add_field(name="Common Friends", value=', '.join(comparison['common_friends']) or 'None', inline=False)
    embed.add_field(name="Common Followers", value=', '.join(comparison['common_followers']) or 'None', inline=False)
    embed.add_field(name="Common Groups", value=', '.join(comparison['common_groups']) or 'None', inline=False)
    embed.add_field(name="Threat Level", value=threat_level, inline=True)
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="profile_intel", description="Detailed Roblox profile inspector with groups, badges, and account details.")
@app_commands.describe(user_id="Roblox User ID")
async def profile_intel(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    profile = get_user_profile(user_id)
    if isinstance(profile, str):
        embed = discord.Embed(title="Error", description=profile, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    groups = get_user_groups(user_id)
    if isinstance(groups, str):
        embed = discord.Embed(title="Error", description="Unable to retrieve group data. Please check the User ID.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    threat_level = profile["risk_level"]
    color = 0x00FF00 if threat_level == "Low" else 0xFFA500 if threat_level == "Medium" else 0xFF0000
    embed = discord.Embed(title=f"Profile Intel: {profile['username']}", color=color)
    embed.add_field(name="Display Name", value=profile['display_name'], inline=True)
    embed.add_field(name="Account Creation Date", value=profile['join_date'], inline=True)
    embed.add_field(name="Account Age", value=f"{profile['account_age_days']} days", inline=True)
    embed.add_field(name="Friends", value=str(profile['friends_count']), inline=True)
    embed.add_field(name="Followers", value=str(profile['followers_count']), inline=True)
    embed.add_field(name="Following", value=str(profile['following_count']), inline=True)
    embed.add_field(name="Total Badges", value=str(profile['total_badges']), inline=True)
    embed.add_field(name="Badges List", value=', '.join(profile['badges_list']) or 'None', inline=False)
    groups_str = '\n'.join([f"- {g['name']} | {g['rank']} ({g['rank_num']})" for g in groups[:10]]) or 'None'
    embed.add_field(name="Groups", value=groups_str, inline=False)
    embed.add_field(name="Past Usernames", value="Not available via API", inline=False)
    embed.add_field(name="Threat Level", value=threat_level, inline=True)
    embed.add_field(name="Risk Factors", value=', '.join(profile['risk_factors']) if profile['risk_factors'] else 'None', inline=False)
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="badge_info", description="Generate a badge graph for a Roblox user.")
@app_commands.describe(user_id="Roblox User ID")
async def badge_info(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    profile = get_user_profile(user_id)
    if isinstance(profile, str):
        embed = discord.Embed(title="Error", description=profile, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    badges_dates = get_user_badges_with_dates(user_id)
    if isinstance(badges_dates, str):
        embed = discord.Embed(title="Error", description=badges_dates, color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    if not badges_dates:
        # Fallback: Show total badges without graph
        embed = discord.Embed(title=f"Badge Info for {profile['username']}", description=f"Total Badges: {profile['total_badges']}\nNo awarded dates available for graph.", color=0xC0C0C0)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
        return
    # Generate graph
    dates = [d.date() for d in badges_dates]
    cumulative = list(range(1, len(dates) + 1))
    plt.figure(figsize=(10, 6))
    plt.plot(dates, cumulative, marker='o', color='#C0C0C0', linewidth=2, markersize=5)
    plt.fill_between(dates, cumulative, color='#E5E5E5', alpha=0.5)
    plt.title(f"Badge Progression for {profile['username']}", fontsize=16, color='#808080')
    plt.xlabel('Awarded Date', fontsize=12, color='#808080')
    plt.ylabel('Cumulative Badge Count', fontsize=12, color='#808080')
    plt.xticks(rotation=45, color='#808080')
    plt.yticks(color='#808080')
    plt.grid(True, color='#D3D3D3', linestyle='--', alpha=0.7)
    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format='png', facecolor='#F5F5F5')
    buf.seek(0)
    plt.close()
    file = discord.File(buf, 'badge_graph.png')
    embed = discord.Embed(title=f"Badge Graph for {profile['username']}", color=0xC0C0C0)
    embed.set_image(url="attachment://badge_graph.png")
    embed.set_footer(text="Information extracted from ISB database.")
    await interaction.followup.send(embed=embed, file=file)

@bot.tree.command(name="tge_user_lookup", description="Lookup Discord user info by username or ID in this server.")
@app_commands.describe(user_input="Discord Username or User ID")
async def tge_user_lookup(interaction: discord.Interaction, user_input: str):
    await interaction.response.defer()
    try:
        # Try to get user by ID first
        user = bot.get_user(int(user_input)) if user_input.isdigit() else None
        if not user:
            # Search by username (case-insensitive, partial match)
            user = discord.utils.find(lambda u: user_input.lower() in u.name.lower() or (u.display_name and user_input.lower() in u.display_name.lower()), interaction.guild.members)
        if not user:
            embed = discord.Embed(title="User Not Found", description="User not found in this server. Ensure the username/ID is correct and the user is a member.", color=0xFF0000)
            embed.set_footer(text="Information extracted from ISB database.")
            await interaction.followup.send(embed=embed)
            return
        embed = discord.Embed(title=f"Discord User Info: {user}", color=0x00FF00)
        embed.add_field(name="Account Created", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
        embed.add_field(name="Server Joined", value=user.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC") if user.joined_at else "Not available", inline=False)
        embed.add_field(name="Roles", value=", ".join([role.name for role in user.roles if role.name != "@everyone"]) or "None", inline=False)
        embed.set_footer(text=f"User ID: {user.id} | Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"tge_user_lookup error: {e}")  # Log for debugging
        embed = discord.Embed(title="Error", description="Unable to retrieve user data. Please check the input.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="discord_user_lookup", description="Advanced Discord user lookup with optional server join date check.")
@app_commands.describe(user_input="Discord Username or User ID", server_id="Optional Server ID to check join date")
async def discord_user_lookup(interaction: discord.Interaction, user_input: str, server_id: str = None):
    await interaction.response.defer()
    try:
        # Try to get user by ID first
        user = bot.get_user(int(user_input)) if user_input.isdigit() else None
        if not user:
            # Search by username globally (limited to bot's guilds)
            for guild in bot.guilds:
                user = discord.utils.find(lambda u: user_input.lower() in u.name.lower() or (u.display_name and user_input.lower() in u.display_name.lower()), guild.members)
                if user:
                    break
        if not user:
            embed = discord.Embed(title="User Not Found", description="User not found. Ensure the username/ID is correct and accessible.", color=0xFF0000)
            embed.set_footer(text="Information extracted from ISB database.")
            await interaction.followup.send(embed=embed)
            return
        embed = discord.Embed(title=f"Discord User Info: {user}", color=0x00FF00)
        embed.add_field(name="Account Created", value=user.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"), inline=False)
        if server_id:
            try:
                guild = bot.get_guild(int(server_id))
                if guild:
                    member = guild.get_member(user.id)
                    if member:
                        embed.add_field(name=f"Joined {guild.name}", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC") if member.joined_at else "Not available", inline=False)
                    else:
                        embed.add_field(name=f"Joined {guild.name}", value="User not in this server", inline=False)
                else:
                    embed.add_field(name="Server Join Date", value="Invalid server ID", inline=False)
            except ValueError:
                embed.add_field(name="Server Join Date", value="Invalid server ID format", inline=False)
        embed.set_footer(text=f"User ID: {user.id} | Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        print(f"discord_user_lookup error: {e}")  # Log for debugging
        embed = discord.Embed(title="Error", description="Unable to retrieve user data. Please check the input.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="calibrate_uplink", description="Calibrate uplink (admin only).")
async def calibrate_uplink(interaction: discord.Interaction):
    if interaction.user.id != AUTHORIZED_USER_ID:
        embed = discord.Embed(title="Unauthorized", description="Access denied.", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if USE_GLOBAL_SYNC:
            synced = await bot.tree.sync()
            embed = discord.Embed(title="Uplink Calibrated", description=f"Successfully synced {len(synced)} command(s) globally.", color=0x00FF00)
        else:
            guild = discord.Object(id=YOUR_GUILD_ID_HERE)
            synced = await bot.tree.sync(guild=guild)
            embed = discord.Embed(title="Uplink Calibrated", description=f"Successfully synced {len(synced)} command(s) to the guild.", color=0x00FF00)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        print(f"Manual sync: {len(synced)} commands synced.")
    except Exception as e:
        embed = discord.Embed(title="Sync Failed", description=f"Failed to sync commands: {e}", color=0xFF0000)
        embed.set_footer(text="Information extracted from ISB database.")
        await interaction.followup.send(embed=embed, ephemeral=True)
        print(f"Manual sync error: {e}")

# --- on_ready event and guild sync ---
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user} (ID: {bot.user.id})")
    try:
        if USE_GLOBAL_SYNC:
            synced = await bot.tree.sync()
            print(f"Successfully synced {len(synced)} command(s) globally.")
        else:
            guild = discord.Object(id=YOUR_GUILD_ID_HERE)
            synced = await bot.tree.sync(guild=guild)
            print(f"Successfully synced {len(synced)} command(s) to guild {guild.id}.")
        if len(synced) == 0:
            print("Warning: No commands synced. Check permissions, guild ID, and bot presence in the guild.")
    except Exception as e:
        print(f"Failed to sync commands on ready: {e}. Ensure bot has permissions and is in the guild.")

# --- Run the bot ---
bot.run(os.environ.get('TOKEN'))


